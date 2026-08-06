"""Pre-deploy checks for Streamlit async DB access (asyncpg InterfaceError guard)."""

from __future__ import annotations

import asyncio
import threading

import pytest


@pytest.mark.quick
def test_run_async_serializes_parallel_threads() -> None:
    """Only one coroutine may run on the shared loop at a time."""
    from ui.async_runner import run_async

    active = 0
    peak = 0
    lock = threading.Lock()

    async def _work() -> None:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        await asyncio.sleep(0.08)
        with lock:
            active -= 1

    errors: list[BaseException] = []

    def _runner() -> None:
        try:
            run_async(lambda: _work(), timeout=5, retries=0)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_runner) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors, f"run_async raised: {errors!r}"
    assert peak == 1, f"expected exclusive execution, peak concurrent={peak}"


@pytest.mark.quick
def test_run_async_retries_interface_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from ui import async_runner

    calls = {"count": 0}
    disposed = {"count": 0}

    class FakeInterfaceError(Exception):
        pass

    async def _flaky_db() -> str:
        calls["count"] += 1
        if calls["count"] == 1:
            raise FakeInterfaceError("another operation is in progress")
        return "ok"

    def _detect(exc: BaseException) -> bool:
        return isinstance(exc, FakeInterfaceError)

    monkeypatch.setattr(async_runner, "_is_asyncpg_interface_error", _detect)
    monkeypatch.setattr(async_runner, "_dispose_engine_pool", lambda: disposed.__setitem__("count", disposed["count"] + 1))

    result = async_runner.run_async(lambda: _flaky_db(), timeout=5, retries=1)

    assert result == "ok"
    assert calls["count"] == 2
    assert disposed["count"] == 1


@pytest.mark.quick
def test_trading_page_loads_db_while_job_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """Trading tab loads portfolio data without inline job banners (status is on other tabs)."""
    from decimal import Decimal
    from unittest.mock import MagicMock

    import ui.dashboard as dashboard
    from app.schemas import AccountOut, InstrumentOut, InstrumentType
    from app.services.market_data_stats import MarketDataStats

    db_calls: list[str] = []

    fake_instrument = InstrumentOut(
        id=1,
        symbol="RELIANCE",
        name="Reliance Industries",
        exchange="NSE",
        instrument_type=InstrumentType.EQUITY,
        is_nifty50=True,
    )

    fake_account = AccountOut(
        name="Paper",
        cash_balance=Decimal("50000"),
        equity_value=Decimal("0"),
        total_value=Decimal("50000"),
        unrealized_pnl=Decimal("0"),
        realized_pnl=Decimal("0"),
        initial_cash=Decimal("50000"),
    )
    fake_stats = MarketDataStats(
        stocks_with_data=0,
        earliest_date=None,
        latest_date=None,
        simulation_universe=None,
        simulation_date=None,
        simulation_saved_at=None,
        simulation_from_cache=False,
        top_patterns=[],
    )

    def _fake_run_async(coro, **kwargs):
        code = getattr(coro, "cr_code", None)
        qual = getattr(code, "co_qualname", getattr(coro, "__qualname__", repr(coro)))
        db_calls.append(qual)
        if "_load_trading_page_data" in qual:
            return ([fake_instrument], fake_account, [], fake_stats, [])
        if "_market_summary" in qual or "_market_data_stats" in qual:
            return fake_stats
        if "load_cached_recommendations" in qual:
            return None
        if "_cleanup_duplicate_session_orders" in qual:
            return {
                "cancelled_plans": 0,
                "cancelled_orders": 0,
                "undone_fills": 0,
            }
        if "_load_order_bracket_context" in qual:
            return ({}, {})
        if "_recommended_symbols_for_session" in qual or "_recommendation_bracket_symbols" in qual:
            return set()
        if "_realized_pnl_after_tax_summary" in qual:
            from app.services.trade_tax import DualBrokerRealizedPnlSummary, RealizedPnlAfterTaxSummary

            zero = RealizedPnlAfterTaxSummary(0.0, 0.0, 0.0, 0.0)
            return DualBrokerRealizedPnlSummary(sharekhan=zero, zerodha=zero)
        if "_orders" in qual or "_trades" in qual or "_candles" in qual or "_nifty250_index_candles" in qual:
            return []
        return []

    sidebar_cm = MagicMock()
    sidebar_cm.__enter__ = MagicMock(return_value=None)
    sidebar_cm.__exit__ = MagicMock(return_value=False)

    def _mock_context(*args, **kwargs):
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=None)
        cm.__exit__ = MagicMock(return_value=False)
        return cm

    monkeypatch.setattr(dashboard, "run_async", _fake_run_async)
    monkeypatch.setattr(
        dashboard,
        "list_jobs",
        lambda **kwargs: [{"progress": 0.5, "message": "Syncing…"}],
    )
    monkeypatch.setattr(dashboard, "is_kind_running", lambda kind: False)
    monkeypatch.setattr(dashboard.st, "info", lambda *a, **k: None)
    monkeypatch.setattr(dashboard.st, "progress", lambda *a, **k: None)
    monkeypatch.setattr(dashboard.st, "sidebar", sidebar_cm)
    monkeypatch.setattr(dashboard.st, "tabs", lambda labels: [_mock_context() for _ in labels])
    monkeypatch.setattr(
        dashboard.st,
        "radio",
        lambda *a, **k: (k.get("options") or (a[1] if len(a) > 1 else ["Positions"]))[0],
    )
    monkeypatch.setattr(
        dashboard.st,
        "columns",
        lambda spec: [_mock_context() for _ in (spec if isinstance(spec, list) else range(spec))],
    )
    def _selectbox(*a, **k):
        opts = k.get("options") or (a[1] if len(a) > 1 else [])
        return opts[0] if opts else ""

    monkeypatch.setattr(dashboard.st, "selectbox", _selectbox)
    monkeypatch.setattr(dashboard.st, "slider", lambda *a, **k: k.get("value", 30))
    monkeypatch.setattr(dashboard.st, "number_input", lambda *a, **k: k.get("value", 1))
    monkeypatch.setattr(dashboard.st, "button", lambda *a, **k: False)
    monkeypatch.setattr(dashboard.st, "header", lambda *a, **k: None)
    monkeypatch.setattr(dashboard.st, "divider", lambda *a, **k: None)
    monkeypatch.setattr(dashboard.st, "subheader", lambda *a, **k: None)
    monkeypatch.setattr(dashboard.st, "markdown", lambda *a, **k: None)
    monkeypatch.setattr(dashboard.st, "caption", lambda *a, **k: None)
    monkeypatch.setattr(dashboard.st, "table", lambda *a, **k: None)
    monkeypatch.setattr(dashboard.st, "write", lambda *a, **k: None)
    monkeypatch.setattr(dashboard.st, "metric", lambda *a, **k: None)
    monkeypatch.setattr(dashboard.st, "dataframe", lambda *a, **k: None)
    monkeypatch.setattr(dashboard.st, "plotly_chart", lambda *a, **k: None)

    dashboard.render_trading_page()

    assert any(
        "_load_trading_page_data" in name for name in db_calls
    ), "render_trading_page must load trading data even while jobs run"


@pytest.mark.quick
def test_load_trading_page_data_is_single_coroutine() -> None:
    import inspect

    from ui.helpers import _load_trading_page_data

    assert inspect.iscoroutinefunction(_load_trading_page_data)


@pytest.mark.quick
def test_ui_session_uses_null_pool() -> None:
    from sqlalchemy.pool import NullPool

    from ui.async_runner import run_async

    async def _pool_class() -> str:
        from app.db.ui_session import get_ui_engine

        engine = await get_ui_engine()
        return type(engine.pool).__name__

    assert run_async(lambda: _pool_class(), timeout=5, retries=0) == NullPool.__name__


@pytest.mark.quick
def test_ui_session_not_shared_with_api_engine() -> None:
    from ui.async_runner import run_async

    async def _check() -> bool:
        from app.db.session import engine as api_engine
        from app.db.ui_session import get_ui_engine

        ui_engine = await get_ui_engine()
        return ui_engine is not api_engine

    assert run_async(lambda: _check(), timeout=5, retries=0) is True


@pytest.mark.quick
def test_async_runner_interface_error_detector() -> None:
    from ui.async_runner import _is_asyncpg_interface_error

    assert _is_asyncpg_interface_error(Exception("another operation is in progress"))
    assert not _is_asyncpg_interface_error(ValueError("bad value"))

"""Market data stats service tests."""

from __future__ import annotations

import importlib
import inspect
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.quick
def test_market_data_stats_import() -> None:
    from app.services.market_data_stats import MarketDataStats, fetch_market_data_stats, get_market_data_stats

    assert callable(get_market_data_stats)
    assert callable(fetch_market_data_stats)
    assert "stocks_with_data" in MarketDataStats.__dataclass_fields__


@pytest.mark.quick
def test_helpers_imports_without_private_fetch_symbol() -> None:
    """Regression: helpers must not import private _fetch_* at module load (Streamlit cache)."""
    import inspect

    import ui.helpers as helpers

    module_source = inspect.getsource(helpers)
    assert "_fetch_market_data_stats" not in module_source
    load_src = inspect.getsource(helpers._load_trading_page_data)
    assert "get_market_data_stats" in load_src
    assert "ensure_market_data_stats_fresh" in load_src


@pytest.mark.quick
def test_get_market_data_stats_accepts_session_kwarg() -> None:
    from app.services.market_data_stats import get_market_data_stats

    params = inspect.signature(get_market_data_stats).parameters
    assert "session" in params
    assert params["session"].kind in (inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)


@pytest.mark.quick
def test_fetch_market_data_stats_signature() -> None:
    from app.services.market_data_stats import fetch_market_data_stats

    sig = inspect.signature(fetch_market_data_stats)
    assert "session" in sig.parameters
    assert "universe" in sig.parameters


@pytest.mark.quick
@pytest.mark.asyncio
async def test_get_market_data_stats_with_session_does_not_open_ui_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.market_data_stats import MarketDataStats, fetch_market_data_stats, get_market_data_stats

    opened: list[str] = []

    class _FakeCtx:
        async def __aenter__(self):
            opened.append("ui_session")
            return MagicMock()

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr("app.services.market_data_stats.ui_session", lambda: _FakeCtx())

    fake_stats = MarketDataStats(
        stocks_with_data=3,
        earliest_date=None,
        latest_date=None,
        simulation_universe="NIFTY250",
        simulation_date=None,
        simulation_saved_at=None,
        simulation_from_cache=False,
        top_patterns=[],
    )
    fetch = AsyncMock(return_value=fake_stats)
    monkeypatch.setattr("app.services.market_data_stats.fetch_market_data_stats", fetch)

    session = MagicMock(name="shared-session")
    result = await get_market_data_stats(session=session)

    assert result is fake_stats
    fetch.assert_awaited_once_with(session, "NIFTY250")
    assert opened == []


@pytest.mark.quick
@pytest.mark.asyncio
async def test_load_trading_page_data_refreshes_stats_and_uses_session_kwarg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.schemas import AccountOut
    from app.services.market_data_stats import MarketDataStats
    from ui import helpers

    fake_session = MagicMock(name="session")
    refresh = MagicMock()
    monkeypatch.setattr("ui.streamlit_imports.ensure_market_data_stats_fresh", refresh)

    class _FakeUiSession:
        async def __aenter__(self):
            return fake_session

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(helpers, "ui_session", lambda: _FakeUiSession())
    monkeypatch.setattr(helpers, "_list_chart_instruments_for", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        helpers,
        "PaperTradingService",
        lambda session: MagicMock(
            get_account_summary=AsyncMock(
                return_value=AccountOut(
                    name="Paper",
                    cash_balance=0,
                    equity_value=0,
                    total_value=0,
                    unrealized_pnl=0,
                    realized_pnl=0,
                    initial_cash=0,
                )
            ),
            list_positions=AsyncMock(return_value=[]),
        ),
    )
    monkeypatch.setattr(
        "app.services.budget_portfolio.normalize_legacy_paper_account",
        AsyncMock(),
    )
    monkeypatch.setattr(helpers, "_market_summary_for", AsyncMock(return_value=[]))

    get_stats = AsyncMock(
        return_value=MarketDataStats(
            stocks_with_data=10,
            earliest_date=None,
            latest_date=None,
            simulation_universe="NIFTY250",
            simulation_date=None,
            simulation_saved_at=None,
            simulation_from_cache=False,
            top_patterns=[],
        )
    )
    monkeypatch.setattr(
        "app.services.market_data_stats.get_market_data_stats",
        get_stats,
    )

    instruments, account, summary, md_stats, positions = await helpers._load_trading_page_data()

    refresh.assert_called_once()
    assert instruments == []
    assert summary == []
    assert positions == []
    assert md_stats is None
    get_stats.assert_not_awaited()

    get_stats.reset_mock()
    refresh.reset_mock()
    instruments, account, summary, md_stats, positions = await helpers._load_trading_page_data(
        include_md_stats=True,
    )
    refresh.assert_called_once()
    assert md_stats is not None
    assert md_stats.stocks_with_data == 10
    get_stats.assert_awaited_once_with("NIFTY250", session=fake_session)


@pytest.mark.quick
def test_helpers_module_loads_when_market_data_stats_is_stale() -> None:
    """Helpers imports only MarketDataStats at load time — not fetch_market_data_stats."""
    import app.services.market_data_stats as real_mds

    importlib.import_module("ui.helpers")
    stale = types.ModuleType("app.services.market_data_stats")
    stale.MarketDataStats = real_mds.MarketDataStats
    sys.modules["app.services.market_data_stats"] = stale

    sys.modules.pop("ui.helpers", None)
    helpers = importlib.import_module("ui.helpers")
    assert hasattr(helpers, "_load_trading_page_data")


@pytest.mark.quick
def test_ensure_market_data_stats_fresh_restores_exports() -> None:
    import app.services.market_data_stats as real_mds

    stale = types.ModuleType("app.services.market_data_stats")
    stale.MarketDataStats = real_mds.MarketDataStats
    sys.modules["app.services.market_data_stats"] = stale

    from ui.streamlit_imports import ensure_market_data_stats_fresh

    ensure_market_data_stats_fresh()
    fresh = sys.modules["app.services.market_data_stats"]
    assert hasattr(fresh, "fetch_market_data_stats")
    assert hasattr(fresh, "get_market_data_stats")


@pytest.mark.quick
@pytest.mark.asyncio
async def test_fetch_market_data_stats_uses_cached_top_patterns_not_live_ranking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Trading page stats must not reload NIFTY250 candles or re-run pattern rankings."""
    from app.services.market_data_stats import fetch_market_data_stats

    live_rank = AsyncMock(side_effect=AssertionError("live ranking should not run"))
    load_candles = AsyncMock(side_effect=AssertionError("load_universe_candles should not run"))

    monkeypatch.setattr(
        "app.services.recommendation_engine.recommendation_pattern_rankings",
        live_rank,
    )
    monkeypatch.setattr(
        "app.services.recommendation_engine.load_universe_candles_from_db",
        load_candles,
    )

    snap_payload = {
        "report": {
            "top_patterns": [
                {"pattern_name": "Hammer", "hit_rate_pct": 62.0},
                {"pattern_name": "NR4", "hit_rate_pct": 58.0},
            ],
        },
    }

    class _Snap:
        payload = snap_payload

    session = MagicMock()
    session.scalar = AsyncMock(
        side_effect=[
            100,  # stocks count
            None,  # earliest
            None,  # latest
            None,  # backtest run today
            None,  # backtest run fallback
            _Snap(),  # recommendation snapshot
        ]
    )

    stats = await fetch_market_data_stats(session, "NIFTY250")

    assert stats.top_patterns == [("Hammer", 62.0), ("NR4", 58.0)]
    from app.services.recommendation_engine import universe_config

    assert stats.recommendation_eval_days == int(universe_config()["eval_days"])
    live_rank.assert_not_called()
    load_candles.assert_not_called()


@pytest.mark.quick
def test_top_patterns_from_backtest_payload_respects_min_hit_rate() -> None:
    from app.services.market_data_stats import _top_patterns_from_backtest_payload

    payload = {
        "eval_days": 15,
        "lookback_days": 20,
        "stock_count": 10,
        "universe": "NIFTY250",
        "symbols": [],
        "patterns": [
            {
                "pattern_id": "a",
                "pattern_name": "Strong",
                "total_correct": 8,
                "total_signals": 10,
                "daily_scores": [],
                "stock_correct": {},
                "stock_signals": {},
                "day_details": [],
            },
            {
                "pattern_id": "b",
                "pattern_name": "Weak",
                "total_correct": 4,
                "total_signals": 10,
                "daily_scores": [],
                "stock_correct": {},
                "stock_signals": {},
                "day_details": [],
            },
        ],
    }

    top = _top_patterns_from_backtest_payload(payload, min_hit_rate=55.0, top_n=3)

    assert top == [("Strong", 80.0)]


@pytest.mark.quick
def test_streamlit_reload_restores_fetch_market_data_stats() -> None:
    import app.services.market_data_stats as real_mds

    stale = types.ModuleType("app.services.market_data_stats")
    stale.MarketDataStats = real_mds.MarketDataStats
    sys.modules["app.services.market_data_stats"] = stale

    from ui.streamlit_imports import ensure_fresh_ui_modules

    ensure_fresh_ui_modules()

    fresh = importlib.import_module("app.services.market_data_stats")
    assert hasattr(fresh, "fetch_market_data_stats")
    sys.modules.pop("ui.helpers", None)
    importlib.import_module("ui.helpers")

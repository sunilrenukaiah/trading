"""Auto-backfill market data before hard refresh when history is short."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.audit_types import AuditStatus
from app.services.backtest import min_candles_for_simulation


@pytest.mark.quick
def test_min_candles_for_simulation() -> None:
    assert min_candles_for_simulation(20, 30) == 51


@pytest.mark.quick
def test_required_backfill_calendar_days_covers_simulation(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.backtest import required_backfill_calendar_days
    from app.services import ingestion

    lookback, eval_days = 20, 30
    min_trading = min_candles_for_simulation(lookback, eval_days)
    calendar = required_backfill_calendar_days(lookback, eval_days)
    assert calendar >= 90
    assert ingestion.effective_backfill_days() >= calendar

    fixed_end = date(2026, 7, 29)
    monkeypatch.setattr(ingestion, "market_data_sync_end_date", lambda: fixed_end)
    start, end = ingestion.market_data_date_range()
    short_start = fixed_end - timedelta(days=90)
    assert start < short_start
    assert (end - start).days >= min_trading


@pytest.mark.quick
async def test_ensure_simulation_candle_data_skips_sync_when_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    import ui.helpers as helpers

    sync = AsyncMock(return_value={"candles_upserted": 0})
    monkeypatch.setattr(helpers, "sync_latest", sync)
    monkeypatch.setattr(
        "app.services.backtest.count_symbols_ready_for_simulation",
        AsyncMock(return_value=(10, 250, 51)),
    )
    monkeypatch.setattr(helpers, "ui_session", lambda: _FakeSessionCtx())

    ok = await helpers._ensure_simulation_candle_data("NIFTY250")

    assert ok is True
    sync.assert_not_called()


@pytest.mark.quick
async def test_ensure_simulation_candle_data_backfills_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    import ui.helpers as helpers

    sync = AsyncMock(return_value={"candles_upserted": 5000})
    counts = AsyncMock(side_effect=[(0, 250, 51), (250, 250, 51)])
    monkeypatch.setattr(helpers, "sync_latest", sync)
    monkeypatch.setattr("app.services.backtest.count_symbols_ready_for_simulation", counts)
    monkeypatch.setattr(helpers, "ui_session", lambda: _FakeSessionCtx())

    ok = await helpers._ensure_simulation_candle_data("NIFTY250", progress_callback=lambda *_: None)

    assert ok is True
    sync.assert_awaited_once()


@pytest.mark.quick
async def test_run_backtest_force_refresh_ensures_data_first(monkeypatch: pytest.MonkeyPatch) -> None:
    import ui.helpers as helpers

    ensure = AsyncMock(return_value=True)
    monkeypatch.setattr(helpers, "_ensure_simulation_candle_data", ensure)
    monkeypatch.setattr(helpers, "_load_cached_simulation", AsyncMock(return_value=(None, None, None)))

    fake_report = MagicMock()
    fake_report.patterns = []

    class _FakeEngine:
        async def run(self, *_args, **_kwargs):
            return fake_report

        async def persist(self, *_args, **_kwargs):
            raise AssertionError("persist should not run when patterns empty")

    monkeypatch.setattr("app.services.backtest_loader.BacktestEngine", lambda **_kwargs: _FakeEngine())
    monkeypatch.setattr(helpers, "ui_session", lambda: _FakeSessionCtx())

    recorded: list[AuditStatus] = []

    def fake_record(_action, _component, status, **_kwargs):
        recorded.append(status)
        return 1

    monkeypatch.setattr("app.services.audit.schedule_audit_event", fake_record)

    result = await helpers._run_backtest(universe="NIFTY250", force_refresh=True)

    ensure.assert_awaited_once_with("NIFTY250", None)
    assert result == (None, None)
    assert AuditStatus.SKIPPED in recorded
    assert AuditStatus.SUCCESS not in recorded


class _FakeSessionCtx:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *_args):
        return False

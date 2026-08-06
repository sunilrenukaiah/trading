"""Paper trading retention window and prune tests."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from app.defaults import DEFAULT_PAPER_TRADING_RETENTION_DAYS
from app.services.paper_trading_retention import (
    filter_closed_within_window,
    prune_paper_trading_history,
    prune_paper_trading_history_if_due,
    retention_cutoff,
    retention_window_start,
)
from app.services.paper_trading_trend import ClosedTradeRow


def _trade(
    symbol: str,
    *,
    closed_date: date,
    pnl: float = 100.0,
) -> ClosedTradeRow:
    return ClosedTradeRow(
        symbol=symbol,
        pattern_name="Hammer",
        closed_date=closed_date,
        recommendation_date=closed_date,
        status="Target Hit",
        entry_price=100.0,
        exit_price=100.0 + pnl,
        shares=10,
        realized_pnl=pnl,
        return_pct=pnl,
        source="bracket",
    )


@pytest.mark.quick
def test_retention_cutoff_uses_ist() -> None:
    now = datetime(2026, 7, 30, 18, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    cutoff = retention_cutoff(days=30, now=now)
    assert cutoff.date() == date(2026, 6, 30)
    assert retention_window_start(days=30, now=now) == date(2026, 6, 30)


@pytest.mark.quick
def test_filter_closed_within_window() -> None:
    now = datetime(2026, 7, 30, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    rows = [
        _trade("OLD", closed_date=date(2026, 6, 1), pnl=50.0),
        _trade("IN", closed_date=date(2026, 7, 15), pnl=200.0),
        _trade("EDGE", closed_date=retention_window_start(days=30, now=now), pnl=10.0),
    ]
    filtered = filter_closed_within_window(rows, days=30, now=now)
    symbols = {r.symbol for r in filtered}
    assert symbols == {"IN", "EDGE"}


@pytest.mark.quick
@pytest.mark.asyncio
async def test_prune_paper_trading_history_deletes_in_order() -> None:
    session = AsyncMock()
    plan_result = MagicMock(rowcount=2)
    trade_result = MagicMock(rowcount=5)
    order_result = MagicMock(rowcount=7)

    async def _execute(stmt):
        sql = str(stmt)
        if "paper_trade_plans" in sql:
            return plan_result
        if "paper_trades" in sql:
            return trade_result
        return order_result

    session.execute = AsyncMock(side_effect=_execute)
    session.commit = AsyncMock()

    async def _referenced(_session):
        return {42, 99}

    from app.services import paper_trading_retention as mod

    mod._referenced_order_ids = AsyncMock(return_value={42, 99})

    stats = await prune_paper_trading_history(
        session,
        retention_days=30,
        now=datetime(2026, 7, 30, 18, 0, tzinfo=ZoneInfo("Asia/Kolkata")),
    )

    assert stats["plans_deleted"] == 2
    assert stats["trades_deleted"] == 5
    assert stats["orders_deleted"] == 7
    assert stats["retention_days"] == 30
    session.commit.assert_awaited_once()


@pytest.mark.quick
@pytest.mark.asyncio
async def test_prune_if_due_skipped_before_post_session_cutoff() -> None:
    session = AsyncMock()
    before_cutoff = datetime(2026, 7, 30, 15, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
    result = await prune_paper_trading_history_if_due(session, now=before_cutoff)
    assert result is None
    session.execute.assert_not_awaited()


@pytest.mark.quick
@pytest.mark.asyncio
async def test_prune_if_due_runs_after_post_session_cutoff() -> None:
    session = AsyncMock()
    plan_result = MagicMock(rowcount=0)
    trade_result = MagicMock(rowcount=0)
    order_result = MagicMock(rowcount=0)

    async def _execute(stmt):
        sql = str(stmt)
        if "paper_trade_plans" in sql:
            return plan_result
        if "paper_trades" in sql:
            return trade_result
        return order_result

    session.execute = AsyncMock(side_effect=_execute)
    session.commit = AsyncMock()

    from app.services import paper_trading_retention as mod

    mod._referenced_order_ids = AsyncMock(return_value=set())

    after_cutoff = datetime(2026, 7, 30, 16, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    result = await prune_paper_trading_history_if_due(session, now=after_cutoff)
    assert result is not None
    assert result["retention_days"] == DEFAULT_PAPER_TRADING_RETENTION_DAYS
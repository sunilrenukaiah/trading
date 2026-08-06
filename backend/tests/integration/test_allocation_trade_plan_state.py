"""Tests for recommendation allocation placed-order detection."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.models import TradePlanStatus


@pytest.mark.quick
@pytest.mark.asyncio
async def test_allocation_trade_plan_state_matches_entry_order_session_day() -> None:
    """Plans placed today must show as placed even if recommendation_date differs."""
    from ui.helpers import _load_allocation_trade_plan_state

    plan = MagicMock()
    plan.instrument = MagicMock()
    plan.instrument.symbol = "TCS"
    plan.status = TradePlanStatus.PENDING_ENTRY
    plan.recommendation_date = date(2026, 7, 30)
    plan.entry_order_id = 99

    order = MagicMock()
    order.created_at = datetime(2026, 7, 31, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))

    session = AsyncMock()
    session.scalars = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=[plan])))
    session.get = AsyncMock(return_value=order)

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)

    with patch("ui.helpers.ui_session", return_value=cm), patch(
        "app.services.paper_trading.PaperTradingService"
    ) as paper_cls, patch(
        "app.services.market_calendar.active_market_session_date",
        return_value=date(2026, 7, 31),
    ), patch(
        "app.services.market_calendar.current_session_date",
        return_value=date(2026, 7, 31),
    ):
        paper_cls.return_value.get_default_account = AsyncMock(return_value=MagicMock(id=1))
        placed, status = await _load_allocation_trade_plan_state(
            date(2026, 7, 31),
            ["TCS", "LT"],
        )

    assert "TCS" in placed
    assert status["TCS"] == "Pending entry"


@pytest.mark.quick
@pytest.mark.asyncio
async def test_position_bracket_levels_include_pending_entry() -> None:
    """Target/stop must show while entry is pending or after fill before OPEN sync."""
    from ui.helpers import _load_position_bracket_levels

    plan = MagicMock()
    plan.instrument = MagicMock()
    plan.instrument.symbol = "SBIN"
    plan.target_price = Decimal("1036.53")
    plan.stop_loss_price = Decimal("999.49")
    plan.status = TradePlanStatus.PENDING_ENTRY

    session = AsyncMock()
    session.scalars = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=[plan])))

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)

    paper = MagicMock()
    paper.match_pending_limit_orders = AsyncMock(return_value=0)
    paper.get_default_account = AsyncMock(return_value=MagicMock(id=1))

    trade_plans = MagicMock()
    trade_plans._sync_entries_from_orders = AsyncMock(return_value=0)

    with patch("ui.helpers.ui_session", return_value=cm), patch(
        "app.services.paper_trading.PaperTradingService",
        return_value=paper,
    ), patch(
        "app.services.trade_plans.TradePlanService",
        return_value=trade_plans,
    ):
        levels = await _load_position_bracket_levels()

    assert levels["SBIN"] == (1036.53, 999.49)
    paper.match_pending_limit_orders.assert_awaited_once()
    trade_plans._sync_entries_from_orders.assert_awaited_once()

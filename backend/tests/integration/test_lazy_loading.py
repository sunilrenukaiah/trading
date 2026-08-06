"""Tests for UI lazy-loading helpers and deferred DB work."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.models import PaperTradePlan, TradePlanStatus
from app.services.budget_allocator import AllocationLine, BudgetAllocationReport
from app.services.midday_recommendations import (
    MiddayActionKind,
    action_kind_for_plan_status,
    is_midday_action_applied,
)


def _sample_line(
    symbol: str = "TCS",
    *,
    buy: float = 100.0,
    target: float = 110.0,
    stop: float = 95.0,
) -> AllocationLine:
    return AllocationLine(
        symbol=symbol,
        cap_tier="Large Cap",
        shares=1,
        buy_price=buy,
        investment=buy,
        stop_loss=stop,
        model_target_price=target,
        actual_sell_price=target,
        expected_profit=10,
        gross_profit=10,
        profit_before_tax=10,
        total_charges=0,
        stcg_tax=0,
        net_profit_after_tax=10,
        max_loss=5,
        weight_pct=10,
        pattern_name="TestPattern",
        confidence_score=0.8,
    )


def _allocation(*symbols: str) -> BudgetAllocationReport:
    lines = [_sample_line(sym) for sym in symbols]
    return BudgetAllocationReport(
        budget_inr=100000,
        total_invested=sum(line.investment for line in lines),
        cash_remaining=100000 - sum(line.investment for line in lines),
        expected_profit=10 * len(lines),
        expected_return_pct=1.0,
        total_gross_profit=10 * len(lines),
        total_charges=0,
        total_stcg_tax=0,
        total_net_profit_after_tax=10 * len(lines),
        max_portfolio_loss=5 * len(lines),
        lines=lines,
    )


@pytest.mark.quick
def test_action_kind_for_plan_status_maps_open_and_pending() -> None:
    assert action_kind_for_plan_status("Open") == MiddayActionKind.OPEN_CALIBRATE
    assert action_kind_for_plan_status("Pending entry") == MiddayActionKind.PENDING_CALIBRATE
    assert action_kind_for_plan_status(None) == MiddayActionKind.NEW
    assert action_kind_for_plan_status("Target hit") == MiddayActionKind.NEW


@pytest.mark.quick
@pytest.mark.asyncio
async def test_load_trading_page_data_summary_and_stats_are_optional(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.schemas import AccountOut
    from ui import helpers

    fake_session = MagicMock(name="session")
    monkeypatch.setattr("ui.streamlit_imports.ensure_market_data_stats_fresh", MagicMock())
    monkeypatch.setattr("ui.streamlit_imports.ensure_market_data_stats_fresh", MagicMock())

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
    summary = AsyncMock(return_value=[MagicMock()])
    monkeypatch.setattr(helpers, "_market_summary_for", summary)
    stats = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr("app.services.market_data_stats.get_market_data_stats", stats)

    _, _, loaded_summary, md_stats, _ = await helpers._load_trading_page_data()
    assert loaded_summary == []
    assert md_stats is None
    summary.assert_not_awaited()
    stats.assert_not_awaited()

    await helpers._load_trading_page_data(include_summary=True, include_md_stats=True)
    summary.assert_awaited_once()
    stats.assert_awaited_once()


@pytest.mark.quick
@pytest.mark.asyncio
async def test_load_midday_place_state_marks_open_calibrated_symbol() -> None:
    """Open plan with mid-day target/stop should show as already applied."""
    from ui.helpers import _load_midday_place_state

    line = _sample_line("NTPCGREEN", buy=90.71, target=94.16, stop=86.96)
    allocation = _allocation("NTPCGREEN")
    allocation.lines[0] = line

    plan = PaperTradePlan(
        account_id=1,
        instrument_id=1,
        recommendation_date=date(2026, 8, 5),
        shares=6,
        entry_limit_price=Decimal("88.00"),
        target_price=Decimal("94.16"),
        stop_loss_price=Decimal("86.96"),
        status=TradePlanStatus.OPEN,
        entry_order_id=42,
    )
    plan.instrument = MagicMock()
    plan.instrument.symbol = "NTPCGREEN"

    order = MagicMock()
    order.created_at = datetime(2026, 8, 5, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))

    session = AsyncMock()
    scalars_result = MagicMock()
    scalars_result.all = MagicMock(return_value=[plan])
    session.scalars = AsyncMock(return_value=scalars_result)
    session.get = AsyncMock(return_value=order)

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)

    with patch("ui.helpers.ui_session", return_value=cm), patch(
        "app.services.paper_trading.PaperTradingService"
    ) as paper_cls, patch(
        "app.services.market_calendar.active_market_session_date",
        return_value=date(2026, 8, 5),
    ), patch(
        "app.services.market_calendar.current_session_date",
        return_value=date(2026, 8, 5),
    ):
        paper_cls.return_value.get_default_account = AsyncMock(return_value=MagicMock(id=1))
        applied, status = await _load_midday_place_state(date(2026, 8, 5), allocation)

    assert "NTPCGREEN" in applied
    assert status["NTPCGREEN"] == "Open"
    assert is_midday_action_applied(plan, line, MiddayActionKind.OPEN_CALIBRATE)


@pytest.mark.quick
def test_recommendation_tier_options_lists_non_empty_groups() -> None:
    from ui.dashboard import _recommendation_tier_options

    rec = MagicMock()
    rec.cap_tier = "Large Cap"
    report = MagicMock()
    report.recommendations = [rec]
    report.price_bucket_recommendations = {"Below ₹100": []}

    options = _recommendation_tier_options(report)
    assert options == [("Large Cap", report.recommendations)]

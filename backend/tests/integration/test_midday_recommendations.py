"""Tests for mid-day recommendation analysis."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.models import PaperTradePlan, TradePlanStatus
from app.services.budget_allocator import AllocationLine, BudgetAllocationReport
from app.services.market_calendar import is_midday_analysis_ready
from app.services.midday_recommendations import (
    MiddayActionKind,
    build_midday_comparison_rows,
    is_midday_action_applied,
)
from decimal import Decimal

IST = ZoneInfo("Asia/Kolkata")


def _sample_line(symbol: str = "TCS", *, buy: float = 100.0) -> AllocationLine:
    return AllocationLine(
        symbol=symbol,
        cap_tier="Large Cap",
        shares=1,
        buy_price=buy,
        investment=buy,
        stop_loss=buy - 5,
        model_target_price=buy + 10,
        actual_sell_price=buy + 10,
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


def test_is_midday_analysis_ready_on_trading_day_after_1145():
    wed = datetime(2026, 7, 29, 12, 0, tzinfo=IST)
    assert is_midday_analysis_ready(now=wed) is True


def test_is_midday_analysis_ready_before_1145():
    wed = datetime(2026, 7, 29, 11, 0, tzinfo=IST)
    assert is_midday_analysis_ready(now=wed) is False


def test_build_midday_comparison_rows_new_pick():
    midday = _allocation("TCS")
    morning = _allocation("TCS")
    morning.lines[0].buy_price = 95.0

    rows = build_midday_comparison_rows(
        midday,
        morning_allocation=morning,
        plan_status_by_symbol={},
    )
    assert len(rows) == 1
    assert rows[0].action == MiddayActionKind.NEW
    assert rows[0].morning_buy == 95.0
    assert rows[0].midday_buy == 100.0
    assert rows[0].buy_changed is True


def test_build_midday_comparison_rows_pending_and_open():
    midday = _allocation("TCS", "INFY")
    rows = build_midday_comparison_rows(
        midday,
        plan_status_by_symbol={"TCS": "Pending entry", "INFY": "Open"},
    )
    by_symbol = {row.symbol: row for row in rows}
    assert by_symbol["TCS"].action == MiddayActionKind.PENDING_CALIBRATE
    assert by_symbol["INFY"].action == MiddayActionKind.OPEN_CALIBRATE


def test_build_midday_comparison_rows_includes_shares():
    midday = _allocation("TCS")
    midday.lines[0].shares = 42

    rows = build_midday_comparison_rows(midday, plan_status_by_symbol={})
    assert rows[0].shares == 42


def test_midday_comparison_dataframe_columns():
    from ui.midday_recommendations_display import midday_comparison_dataframe

    midday = _allocation("TCS", "INFY")
    rows = build_midday_comparison_rows(midday, plan_status_by_symbol={"TCS": "Open"})
    df = midday_comparison_dataframe(rows)

    assert "Shares" in df.columns
    assert "Midday buy" in df.columns
    assert df.loc[df["Symbol"] == "TCS", "Shares"].iloc[0] == 1


def _mock_plan(
    *,
    status: TradePlanStatus,
    buy: float = 100.0,
    target: float = 110.0,
    stop: float = 95.0,
) -> PaperTradePlan:
    plan = PaperTradePlan(
        account_id=1,
        instrument_id=1,
        recommendation_date=datetime(2026, 8, 5).date(),
        shares=1,
        entry_limit_price=Decimal(str(buy)),
        target_price=Decimal(str(target)),
        stop_loss_price=Decimal(str(stop)),
        status=status,
    )
    return plan


def test_is_midday_action_applied_new_pick():
    line = _sample_line("TCS")
    plan = _mock_plan(status=TradePlanStatus.OPEN)
    assert is_midday_action_applied(plan, line, MiddayActionKind.NEW) is True
    assert is_midday_action_applied(None, line, MiddayActionKind.NEW) is False


def test_is_midday_action_applied_open_calibrated():
    line = _sample_line("TCS", buy=100.0)
    plan = _mock_plan(status=TradePlanStatus.OPEN, buy=100.0, target=110.0, stop=95.0)
    assert (
        is_midday_action_applied(plan, line, MiddayActionKind.OPEN_CALIBRATE) is True
    )

    plan_morning = _mock_plan(status=TradePlanStatus.OPEN, buy=100.0, target=105.0, stop=97.0)
    assert (
        is_midday_action_applied(plan_morning, line, MiddayActionKind.OPEN_CALIBRATE)
        is False
    )


def test_is_midday_action_applied_pending_calibrated():
    line = _sample_line("TCS", buy=100.0)
    plan = _mock_plan(status=TradePlanStatus.PENDING_ENTRY)
    assert (
        is_midday_action_applied(plan, line, MiddayActionKind.PENDING_CALIBRATE) is True
    )

    plan_old = _mock_plan(
        status=TradePlanStatus.PENDING_ENTRY, buy=95.0, target=110.0, stop=95.0
    )
    assert (
        is_midday_action_applied(plan_old, line, MiddayActionKind.PENDING_CALIBRATE)
        is False
    )

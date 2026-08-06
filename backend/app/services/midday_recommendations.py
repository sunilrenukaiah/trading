"""Mid-day recommendation re-analysis and comparison with morning picks."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.models import PaperTradePlan, TradePlanStatus
from app.services.budget_allocator import AllocationLine, BudgetAllocationReport
from app.services.recommendation_engine import RecommendationReport, StockRecommendation


class MiddayActionKind(str, Enum):
    NEW = "new"
    PENDING_CALIBRATE = "pending_calibrate"
    OPEN_CALIBRATE = "open_calibrate"


_CLOSED_PLAN_STATUSES = frozenset(
    {
        TradePlanStatus.TARGET_HIT,
        TradePlanStatus.STOP_HIT,
        TradePlanStatus.TIME_EXIT,
        TradePlanStatus.CANCELLED,
    }
)


def _price_matches(plan_value: float | None, line_value: float) -> bool:
    if plan_value is None:
        return False
    return round(plan_value, 2) == round(line_value, 2)


def action_kind_for_plan_status(plan_status: str | None) -> MiddayActionKind:
    if plan_status == "Open":
        return MiddayActionKind.OPEN_CALIBRATE
    if plan_status == "Pending entry":
        return MiddayActionKind.PENDING_CALIBRATE
    return MiddayActionKind.NEW


def is_midday_action_applied(
    plan: PaperTradePlan | None,
    line: AllocationLine,
    action: MiddayActionKind,
) -> bool:
    """True when mid-day place/calibrate for this line is already done."""
    if plan is None or plan.status in _CLOSED_PLAN_STATUSES:
        return False

    if action == MiddayActionKind.NEW:
        return True

    if action == MiddayActionKind.PENDING_CALIBRATE:
        if plan.status != TradePlanStatus.PENDING_ENTRY:
            return False
        return (
            _price_matches(float(plan.entry_limit_price), line.buy_price)
            and _price_matches(float(plan.target_price), line.actual_sell_price)
            and _price_matches(float(plan.stop_loss_price), line.stop_loss)
        )

    if action == MiddayActionKind.OPEN_CALIBRATE:
        if plan.status != TradePlanStatus.OPEN:
            return False
        return _price_matches(float(plan.target_price), line.actual_sell_price) and _price_matches(
            float(plan.stop_loss_price), line.stop_loss
        )

    return False


@dataclass(frozen=True)
class MiddayComparisonRow:
    symbol: str
    action: MiddayActionKind
    plan_status: str | None
    shares: int
    pattern_name: str
    morning_buy: float | None
    midday_buy: float
    morning_target: float | None
    midday_target: float
    morning_stop: float | None
    midday_stop: float

    @property
    def buy_changed(self) -> bool:
        if self.morning_buy is None:
            return False
        return round(self.morning_buy, 2) != round(self.midday_buy, 2)

    @property
    def target_changed(self) -> bool:
        if self.morning_target is None:
            return False
        return round(self.morning_target, 2) != round(self.midday_target, 2)

    @property
    def stop_changed(self) -> bool:
        if self.morning_stop is None:
            return False
        return round(self.morning_stop, 2) != round(self.midday_stop, 2)

    @property
    def has_level_changes(self) -> bool:
        return self.buy_changed or self.target_changed or self.stop_changed


def _morning_lookup(
    morning_report: RecommendationReport | None,
    morning_allocation: BudgetAllocationReport | None,
) -> dict[str, tuple[float, float, float]]:
    """symbol -> (buy, target, stop) from morning run."""
    lookup: dict[str, tuple[float, float, float]] = {}

    if morning_allocation is not None:
        for line in morning_allocation.lines:
            lookup[line.symbol.upper()] = (
                float(line.buy_price),
                float(line.actual_sell_price),
                float(line.stop_loss),
            )
        return lookup

    if morning_report is None:
        return lookup

    def _add(rec: StockRecommendation) -> None:
        sym = rec.symbol.upper()
        if sym not in lookup:
            lookup[sym] = (
                float(rec.buy_price),
                float(rec.actual_sell_price),
                float(rec.stop_loss),
            )

    for rec in morning_report.recommendations:
        _add(rec)
    for recs in morning_report.price_bucket_recommendations.values():
        for rec in recs:
            _add(rec)
    return lookup


def build_midday_comparison_rows(
    midday_allocation: BudgetAllocationReport,
    *,
    morning_report: RecommendationReport | None = None,
    morning_allocation: BudgetAllocationReport | None = None,
    plan_status_by_symbol: dict[str, str] | None = None,
) -> list[MiddayComparisonRow]:
    """Build per-line comparison rows for the mid-day analysis table."""
    morning = _morning_lookup(morning_report, morning_allocation)
    plan_status_by_symbol = plan_status_by_symbol or {}
    rows: list[MiddayComparisonRow] = []

    for line in midday_allocation.lines:
        sym = line.symbol.upper()
        morning_levels = morning.get(sym)
        plan_status = plan_status_by_symbol.get(sym) or plan_status_by_symbol.get(line.symbol)

        action = action_kind_for_plan_status(plan_status)

        rows.append(
            MiddayComparisonRow(
                symbol=line.symbol,
                action=action,
                plan_status=plan_status,
                shares=int(line.shares),
                pattern_name=line.pattern_name,
                morning_buy=morning_levels[0] if morning_levels else None,
                midday_buy=float(line.buy_price),
                morning_target=morning_levels[1] if morning_levels else None,
                midday_target=float(line.actual_sell_price),
                morning_stop=morning_levels[2] if morning_levels else None,
                midday_stop=float(line.stop_loss),
            )
        )

    return rows

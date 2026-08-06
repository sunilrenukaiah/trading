"""Allocate daily trading budget across stock recommendations."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from app.services.recommendation_engine import (
    RecommendationReport,
    StockRecommendation,
    all_report_recommendations,
)
from app.services.bracket_utils import is_valid_bracket_levels
from app.services.trade_tax import compute_net_profit

CAP_TIER_ORDER = ("Large Cap", "Mid Cap", "Small Cap")


@dataclass
class AllocationLine:
    symbol: str
    cap_tier: str
    shares: int
    buy_price: float
    investment: float
    stop_loss: float
    model_target_price: float
    actual_sell_price: float
    expected_profit: float
    gross_profit: float
    profit_before_tax: float
    total_charges: float
    stcg_tax: float
    net_profit_after_tax: float
    max_loss: float
    weight_pct: float
    pattern_name: str
    confidence_score: float


@dataclass
class BudgetAllocationReport:
    budget_inr: float
    total_invested: float
    cash_remaining: float
    expected_profit: float
    expected_return_pct: float
    total_gross_profit: float
    total_charges: float
    total_stcg_tax: float
    total_net_profit_after_tax: float
    max_portfolio_loss: float
    lines: list[AllocationLine]
    skipped_invalid: list[str] = field(default_factory=list)
    skipped_unprofitable: list[str] = field(default_factory=list)
    backfilled_symbols: list[str] = field(default_factory=list)


def allocation_trading_blocked(allocation: BudgetAllocationReport) -> tuple[bool, str | None]:
    """True when portfolio expected profit is not positive after charges."""
    if not allocation.lines:
        return False, None
    if allocation.total_net_profit_after_tax <= 0 or allocation.expected_profit <= 0:
        return (
            True,
            "Expected profit is negative after brokerage, STT, and other charges. "
            "Do not place orders on this batch — re-run analysis with wider targets "
            "or wait for setups with larger upside.",
        )
    return False, None


def is_profitable_allocation_line(line: AllocationLine) -> bool:
    return line.net_profit_after_tax > 0 and line.expected_profit > 0


def _empty_allocation(budget_inr: float) -> BudgetAllocationReport:
    return BudgetAllocationReport(
        budget_inr=budget_inr,
        total_invested=0,
        cash_remaining=budget_inr,
        expected_profit=0,
        expected_return_pct=0,
        total_gross_profit=0,
        total_charges=0,
        total_stcg_tax=0,
        total_net_profit_after_tax=0,
        max_portfolio_loss=0,
        lines=[],
    )


def _valid_bracket(rec: StockRecommendation) -> bool:
    return is_valid_bracket_levels(rec.buy_price, rec.actual_sell_price, rec.stop_loss)


def _valid_recs_by_tier(report: RecommendationReport) -> dict[str, list[StockRecommendation]]:
    """All valid recommendations grouped by tier, best confidence first."""
    by_tier: dict[str, list[StockRecommendation]] = defaultdict(list)
    seen: set[str] = set()
    for rec in all_report_recommendations(report):
        if rec.symbol in seen or not _valid_bracket(rec):
            continue
        seen.add(rec.symbol)
        by_tier[rec.cap_tier].append(rec)
    for tier in by_tier:
        by_tier[tier].sort(key=lambda r: r.confidence_score, reverse=True)
    return by_tier


def _tier_pick_count(report: RecommendationReport) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for rec in report.recommendations:
        counts[rec.cap_tier] += 1
    return counts


def _allocate_line(
    rec: StockRecommendation,
    slice_budget: float,
    budget_inr: float,
) -> AllocationLine | None:
    if not _valid_bracket(rec):
        return None

    shares = int(slice_budget // rec.buy_price) if rec.buy_price > 0 else 0
    if shares <= 0:
        return None

    investment = round(shares * rec.buy_price, 2)
    prob = rec.pattern_hit_rate_30d / 100
    tax = compute_net_profit(shares, rec.buy_price, rec.actual_sell_price)
    if tax.net_profit_after_tax <= 0:
        return None
    exp_profit = round(tax.net_profit_after_tax * prob, 2)
    loss = round(shares * (rec.buy_price - rec.stop_loss), 2)

    return AllocationLine(
        symbol=rec.symbol,
        cap_tier=rec.price_bucket or rec.cap_tier,
        shares=shares,
        buy_price=rec.buy_price,
        investment=investment,
        stop_loss=rec.stop_loss,
        model_target_price=rec.sell_price,
        actual_sell_price=rec.actual_sell_price,
        expected_profit=exp_profit,
        gross_profit=tax.gross_profit,
        profit_before_tax=tax.profit_before_tax,
        total_charges=tax.total_charges,
        stcg_tax=tax.stcg_tax,
        net_profit_after_tax=tax.net_profit_after_tax,
        max_loss=loss,
        weight_pct=round(investment / budget_inr * 100, 1) if budget_inr else 0,
        pattern_name=rec.pattern_name,
        confidence_score=rec.confidence_score,
    )


def _add_line(
    line: AllocationLine,
    *,
    lines: list[AllocationLine],
    totals: dict[str, float],
) -> None:
    lines.append(line)
    totals["invested"] += line.investment
    totals["expected_profit"] += line.expected_profit
    totals["gross"] += line.gross_profit
    totals["charges"] += line.total_charges
    totals["stcg"] += line.stcg_tax
    totals["net"] += line.net_profit_after_tax
    totals["max_loss"] += line.max_loss


def allocate_budget(
    report: RecommendationReport,
    budget_inr: float,
    *,
    min_confidence: float = 50.0,
    tier_budget_split_pct: float = 33.33,
) -> BudgetAllocationReport:
    """
    Split budget equally across cap tiers, then weight within each tier by confidence.
    Invalid brackets (target ≤ entry) are skipped and replaced by the next valid pick
    in the same tier from price-bucket / expanded recommendations when available.
    """
    skipped_invalid = [
        rec.symbol
        for rec in report.recommendations
        if rec.confidence_score >= min_confidence and not _valid_bracket(rec)
    ]
    tier_targets = _tier_pick_count(report)
    valid_by_tier = _valid_recs_by_tier(report)
    primary_symbols = {rec.symbol for rec in report.recommendations}
    backfilled: list[str] = []

    bucket_groups = {
        label: [
            r
            for r in bucket_recs
            if r.confidence_score >= min_confidence
            and r.symbol not in {x.symbol for x in report.recommendations}
            and _valid_bracket(r)
        ]
        for label, bucket_recs in (report.price_bucket_recommendations or {}).items()
        if bucket_recs
    }
    bucket_groups = {label: recs for label, recs in bucket_groups.items() if recs}

    if not tier_targets and not bucket_groups:
        empty = _empty_allocation(budget_inr)
        empty.skipped_invalid = skipped_invalid
        return empty

    skipped_unprofitable: list[str] = []
    tier_split = max(min(tier_budget_split_pct, 100.0), 0.0) / 100.0
    tier_budget = budget_inr * tier_split

    lines: list[AllocationLine] = []
    totals = {
        "invested": 0.0,
        "expected_profit": 0.0,
        "gross": 0.0,
        "charges": 0.0,
        "stcg": 0.0,
        "net": 0.0,
        "max_loss": 0.0,
    }
    used_symbols: set[str] = set()
    primary_order = {rec.symbol: idx for idx, rec in enumerate(report.recommendations)}

    def _tier_sort_key(rec: StockRecommendation) -> tuple:
        if rec.symbol in primary_order:
            return (0, primary_order[rec.symbol])
        return (1, -rec.confidence_score)

    for tier in CAP_TIER_ORDER:
        need = tier_targets.get(tier, 0)
        if need <= 0:
            continue

        tier_picks: list[StockRecommendation] = []
        candidates = sorted(valid_by_tier.get(tier, []), key=_tier_sort_key)
        for rec in candidates:
            if rec.symbol in used_symbols:
                continue
            tier_picks.append(rec)
            used_symbols.add(rec.symbol)
            if rec.symbol not in primary_symbols:
                backfilled.append(rec.symbol)
            if len(tier_picks) >= need:
                break

        if not tier_picks:
            continue

        weights = [max(r.confidence_score, 1) for r in tier_picks]
        weight_sum = sum(weights)

        for rec, weight in zip(tier_picks, weights):
            slice_budget = tier_budget * (weight / weight_sum)
            line = _allocate_line(rec, slice_budget, budget_inr)
            if line is None:
                shares = int(slice_budget // rec.buy_price) if rec.buy_price > 0 else 0
                if shares > 0:
                    tax = compute_net_profit(shares, rec.buy_price, rec.actual_sell_price)
                    if tax.net_profit_after_tax <= 0:
                        skipped_unprofitable.append(rec.symbol)
                continue
            _add_line(line, lines=lines, totals=totals)

    cash_for_buckets = round(budget_inr - totals["invested"], 2)
    if bucket_groups and cash_for_buckets > 0:
        per_bucket = cash_for_buckets / len(bucket_groups)
        for bucket_recs in bucket_groups.values():
            weights = [max(r.confidence_score, 1) for r in bucket_recs]
            weight_sum = sum(weights)
            for rec, weight in zip(bucket_recs, weights):
                if rec.symbol in used_symbols:
                    continue
                slice_budget = per_bucket * (weight / weight_sum)
                line = _allocate_line(rec, slice_budget, budget_inr)
                if line is None:
                    shares = int(slice_budget // rec.buy_price) if rec.buy_price > 0 else 0
                    if shares > 0:
                        tax = compute_net_profit(shares, rec.buy_price, rec.actual_sell_price)
                        if tax.net_profit_after_tax <= 0:
                            skipped_unprofitable.append(rec.symbol)
                    continue
                used_symbols.add(rec.symbol)
                _add_line(line, lines=lines, totals=totals)

    cash_remaining = round(budget_inr - totals["invested"], 2)
    expected_return_pct = (
        round(totals["expected_profit"] / budget_inr * 100, 2) if budget_inr else 0
    )

    return BudgetAllocationReport(
        budget_inr=budget_inr,
        total_invested=round(totals["invested"], 2),
        cash_remaining=cash_remaining,
        expected_profit=round(totals["expected_profit"], 2),
        expected_return_pct=expected_return_pct,
        total_gross_profit=round(totals["gross"], 2),
        total_charges=round(totals["charges"], 2),
        total_stcg_tax=round(totals["stcg"], 2),
        total_net_profit_after_tax=round(totals["net"], 2),
        max_portfolio_loss=round(totals["max_loss"], 2),
        lines=lines,
        skipped_invalid=skipped_invalid,
        skipped_unprofitable=sorted(set(skipped_unprofitable)),
        backfilled_symbols=backfilled,
    )

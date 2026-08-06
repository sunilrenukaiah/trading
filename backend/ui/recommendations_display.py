"""Format recommendation and budget allocation reports for Streamlit."""

from __future__ import annotations

import math

import pandas as pd

from app.defaults import DEFAULT_MAX_TARGET_PROFIT_PCT
from app.schemas import PositionOut
from app.services.applicable_rates import get_applicable_rates
from app.services.budget_allocator import BudgetAllocationReport
from app.services.recommendation_engine import RecommendationReport, StockRecommendation
from app.services.trade_tax import compute_net_profit


def _format_inr(value: float) -> str:
    if value is None or not math.isfinite(float(value)):
        return "—"
    return f"₹{value:,.2f}"


def recommendation_investment_dataframe(
    allocation: BudgetAllocationReport,
    positions: list[PositionOut] | None = None,
    plan_status_by_symbol: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Merge allocation plan with live paper positions for the Trading tab summary."""
    pos_map = {p.symbol: p for p in (positions or [])}
    plan_map = plan_status_by_symbol or {}
    rows = []
    for line in allocation.lines:
        pos = pos_map.get(line.symbol)
        held_qty = int(pos.quantity) if pos else 0
        invested_held = float(pos.avg_cost * pos.quantity) if pos and held_qty else 0.0
        plan_status = plan_map.get(line.symbol)
        if plan_status:
            status = plan_status
        elif held_qty >= line.shares:
            status = "Open (filled)"
        elif held_qty > 0:
            status = "Partial"
        else:
            status = "Not placed"
        rows.append(
            {
                "Stock": line.symbol,
                "Cap tier": line.cap_tier,
                "Shares (plan)": line.shares,
                "Shares (held)": held_qty,
                "Plan status": status,
                "Invested (plan)": _format_inr(line.investment),
                "Invested (held)": _format_inr(invested_held) if invested_held else "—",
                "Pattern": line.pattern_name,
            }
        )
    return pd.DataFrame(rows)


def eod_analysis_dataframe(report) -> pd.DataFrame:
    rows = []
    for row in report.rows:
        rows.append(
            {
                "Stock": row.symbol,
                "Pattern": row.pattern_name,
                "Shares": row.shares,
                "Status": row.status,
                "Entry": _format_inr(row.entry_price) if row.entry_price else "—",
                "Target": _format_inr(row.target_price),
                "Stop loss": _format_inr(row.stop_loss_price),
                "Exit": _format_inr(row.exit_price) if row.exit_price else "—",
                "P&L": _format_inr(row.realized_pnl) if row.realized_pnl is not None else "—",
            }
        )
    return pd.DataFrame(rows)


def format_sell_target_display(
    actual_sell_price: float,
    model_target_price: float,
) -> str:
    """Bracket sell price and full model target, e.g. ``₹282.00 (₹312.00)``."""
    actual_str = _format_inr(actual_sell_price)
    model_str = _format_inr(model_target_price)
    if actual_str == "—" and model_str == "—":
        return "—"
    return f"{actual_str} ({model_str})"


def allocation_summary_rows(allocation: BudgetAllocationReport) -> list[dict]:
    """Compact row dicts for the actionable allocation table."""
    return [
        {
            "symbol": line.symbol,
            "cap_tier": line.cap_tier,
            "shares": line.shares,
            "buy_price": line.buy_price,
            "investment": line.investment,
            "pattern_name": line.pattern_name,
            "line": line,
        }
        for line in allocation.lines
    ]


def allocation_simulation_dataframe(allocation: BudgetAllocationReport) -> pd.DataFrame:
    """Compact allocation table for what-if budget simulation (no trade actions)."""
    rows = []
    for line in allocation.lines:
        rows.append(
            {
                "Cap tier": line.cap_tier,
                "Stock": line.symbol,
                "Shares": line.shares,
                "Buy price": _format_inr(line.buy_price),
                "Stop loss": _format_inr(line.stop_loss),
                "Sell target": format_sell_target_display(
                    line.actual_sell_price,
                    line.model_target_price,
                ),
                "Investment": _format_inr(line.investment),
                "Expected profit*": _format_inr(line.expected_profit),
                "Pattern": line.pattern_name,
            }
        )
    return pd.DataFrame(rows)


def patterns_dataframe(report: RecommendationReport) -> pd.DataFrame:
    rows = []
    for p in report.top_patterns:
        rows.append(
            {
                "Pattern": p.pattern_name,
                "15d hit rate %": p.hit_rate_pct,
                "Correct / Signals": f"{p.total_correct}/{p.total_signals}",
                "Avg correct/day": p.avg_daily_score,
            }
        )
    return pd.DataFrame(rows)


def recommendations_dataframe(
    recommendations: list[StockRecommendation],
    *,
    max_target_profit_pct: float = DEFAULT_MAX_TARGET_PROFIT_PCT,
) -> pd.DataFrame:
    from app.services.recommendation_engine import coerce_stock_recommendation

    rows = []
    for raw in recommendations:
        r = coerce_stock_recommendation(raw)
        tax = compute_net_profit(1, r.buy_price, r.actual_sell_price)
        rows.append(
            {
                "Cap tier": r.cap_tier,
                "Stock": r.symbol,
                "Buy (ref)": f"₹{r.buy_price:,.2f}",
                "Action": r.action,
                "Pattern": r.pattern_name,
                "15d hit %": r.pattern_hit_rate_30d,
                "Confidence": r.confidence_score,
                "Expected move (₹)": f"₹{r.expected_move_inr:,.2f}",
                "Rel volume": (
                    f"{r.relative_volume:.2f}×" if r.relative_volume is not None else "—"
                ),
                "Stop loss": f"₹{r.stop_loss:,.2f}",
                "Resistance": f"₹{r.resistance:,.2f}",
                f"Model target (max {max_target_profit_pct:g}%)": (
                    f"₹{r.sell_price:,.2f} ({r.model_profit_pct:+.1f}%)"
                ),
                "Actual sell price": f"₹{r.actual_sell_price:,.2f} ({r.actual_profit_pct:+.1f}%)",
                "Profit before tax (1 sh)": f"₹{tax.profit_before_tax:,.2f}",
                "Profit after tax (1 sh)": f"₹{tax.net_profit_after_tax:,.2f}",
                "R:R (actual)": r.risk_reward,
                "Also bullish": ", ".join(r.supporting_patterns) if r.supporting_patterns else "—",
            }
        )
    return pd.DataFrame(rows)


def report_recommendations_dataframe(report: RecommendationReport) -> pd.DataFrame:
    return recommendations_dataframe(
        report.recommendations,
        max_target_profit_pct=report.max_target_profit_pct,
    )


def allocation_dataframe(allocation: BudgetAllocationReport) -> pd.DataFrame:
    stcg_pct = get_applicable_rates().stcg_tax_rate * 100
    rows = []
    for line in allocation.lines:
        rows.append(
            {
                "Cap tier": line.cap_tier,
                "Stock": line.symbol,
                "Shares": line.shares,
                "Buy price": f"₹{line.buy_price:,.2f}",
                "Investment": f"₹{line.investment:,.2f}",
                "Weight %": line.weight_pct,
                "Stop loss": f"₹{line.stop_loss:,.2f}",
                "Model target": f"₹{line.model_target_price:,.2f}",
                "Actual sell": f"₹{line.actual_sell_price:,.2f}",
                "Profit before tax": f"₹{line.profit_before_tax:,.2f}",
                "Profit after tax": f"₹{line.net_profit_after_tax:,.2f}",
                "Gross profit": f"₹{line.gross_profit:,.2f}",
                "Charges (STT+stamp+broker+NSE/GST)": f"₹{line.total_charges:,.2f}",
                f"STCG tax ({stcg_pct:g}%)": f"₹{line.stcg_tax:,.2f}",
                "Expected profit*": f"₹{line.expected_profit:,.2f}",
                "Max loss": f"₹{line.max_loss:,.2f}",
                "Pattern": line.pattern_name,
            }
        )
    return pd.DataFrame(rows)


def budget_simulation_comparison_dataframe(
    report: RecommendationReport,
    budgets: list[float],
    *,
    tier_budget_split_pct: float = 33.33,
) -> pd.DataFrame:
    """Share counts per stock across multiple what-if budgets (same picks)."""
    from app.services.budget_allocator import allocate_budget

    normalized = sorted({float(b) for b in budgets if b and b > 0})
    if not normalized:
        return pd.DataFrame()

    shares_by_budget: dict[float, dict[str, int]] = {}
    symbols: set[str] = set()
    for budget_inr in normalized:
        alloc = allocate_budget(
            report,
            budget_inr,
            tier_budget_split_pct=tier_budget_split_pct,
        )
        shares_by_budget[budget_inr] = {line.symbol: line.shares for line in alloc.lines}
        symbols.update(shares_by_budget[budget_inr])

    rows = []
    for symbol in sorted(symbols):
        row: dict[str, object] = {"Stock": symbol}
        for budget_inr in normalized:
            label = f"₹{budget_inr:,.0f}"
            row[label] = shares_by_budget[budget_inr].get(symbol, 0)
        rows.append(row)
    return pd.DataFrame(rows)

"""Format EOD trade analysis reports for Streamlit."""

from __future__ import annotations

import math

import pandas as pd

from app.services.eod_trade_analysis import EodTradeAnalysisReport


def _format_inr(value: float | None) -> str:
    if value is None or not math.isfinite(float(value)):
        return "—"
    return f"₹{value:,.2f}"


def _format_pct(value: float | None) -> str:
    if value is None or not math.isfinite(float(value)):
        return "—"
    return f"{value:+.2f}%"


def trade_analysis_dataframe(report: EodTradeAnalysisReport) -> pd.DataFrame:
    rows = []
    for trade in report.trades:
        rows.append(
            {
                "Stock": trade.symbol,
                "Pattern used": trade.pattern_used,
                "Entry": "Yes" if trade.entry_made else "No",
                "Entry price": _format_inr(trade.entry_price),
                "Touched SL": "Yes" if trade.touched_stop else "No",
                "Touched target": "Yes" if trade.touched_target else "No",
                "Close vs target": trade.close_vs_target,
                "Close Δ vs target": _format_pct(trade.close_vs_target_pct),
                "Day close": _format_inr(trade.day_close),
                "Target": _format_inr(trade.target_price),
                "Status": trade.plan_status,
                "Exit": _format_inr(trade.exit_price),
                "P&L": _format_inr(trade.realized_pnl if trade.realized_pnl is not None else trade.mark_to_market_pnl),
                "Target missed": "Yes" if trade.target_missed else "No",
                "Miss %": f"{trade.target_miss_pct:.2f}%" if trade.target_miss_pct else "—",
            }
        )
    return pd.DataFrame(rows)


def better_patterns_dataframe(report: EodTradeAnalysisReport) -> pd.DataFrame:
    rows = []
    for trade in report.trades:
        for alt in trade.better_patterns:
            rows.append(
                {
                    "Stock": trade.symbol,
                    "Pattern used": trade.pattern_used,
                    "Better pattern": alt.pattern_name,
                    "Correct today": "Yes" if alt.correct else "No",
                    "Predicted close": _format_inr(alt.predicted_close),
                    "Hypothetical exit": _format_inr(alt.hypothetical_exit),
                    "Hypothetical P&L": _format_inr(alt.hypothetical_pnl),
                    "Extra vs actual": _format_inr(alt.pnl_vs_actual),
                }
            )
    return pd.DataFrame(rows)


def missed_target_dataframe(report: EodTradeAnalysisReport) -> pd.DataFrame:
    rows = []
    for trade in report.trades:
        if not trade.target_missed:
            continue
        rows.append(
            {
                "Stock": trade.symbol,
                "Pattern": trade.pattern_used,
                "Target": _format_inr(trade.target_price),
                "Exit / close": _format_inr(trade.exit_price or trade.day_close),
                "Miss below target": f"{trade.target_miss_pct:.2f}%",
                "Status": trade.plan_status,
            }
        )
    return pd.DataFrame(rows)


def executed_trade_reviews_dataframe(report: EodTradeAnalysisReport) -> pd.DataFrame:
    rows = []
    for row in report.executed_trade_reviews:
        rows.append(
            {
                "Stock": row.symbol,
                "Pattern": row.pattern_used,
                "Entry": _format_inr(row.entry_price),
                "Intraday peak": _format_inr(row.day_high),
                "Actual exit": _format_inr(row.exit_price),
                "Pattern target": _format_inr(row.pattern_target),
                "Peak P&L": _format_inr(row.peak_pnl_inr),
                "Actual P&L": _format_inr(row.actual_pnl_inr),
                "Left on table": _format_inr(row.left_on_table_inr),
                "Peak vs exit": _format_pct(row.left_on_table_pct),
                "Exit vs target": _format_pct(row.exit_vs_target_pct),
                "Peak vs target": _format_pct(row.peak_vs_target_pct),
                "Peak hit target": "Yes" if row.peak_reached_target else "No",
                "Status": row.plan_status,
            }
        )
    return pd.DataFrame(rows)


def missed_profitable_trades_dataframe(report: EodTradeAnalysisReport) -> pd.DataFrame:
    rows = []
    for row in report.missed_profitable_trades:
        hit_rate = (
            f"{row.catching_pattern_hit_rate:.1f}%"
            if row.catching_pattern_hit_rate is not None
            else "—"
        )
        rows.append(
            {
                "Stock": row.symbol,
                "Cap tier": row.cap_tier,
                "Day return": _format_pct(row.day_return_pct),
                "Prev close": _format_inr(row.prev_close),
                "Day close": _format_inr(row.day_close),
                "Why we missed it": row.why_missed,
                "Top pattern signals": row.top_pattern_signals,
                "Pattern that would catch it": row.catching_pattern or "—",
                "Pattern hit rate": hit_rate,
            }
        )
    return pd.DataFrame(rows)

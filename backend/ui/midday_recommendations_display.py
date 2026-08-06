"""Streamlit display helpers for mid-day recommendation analysis."""

from __future__ import annotations

import pandas as pd

from app.services.midday_recommendations import MiddayActionKind, MiddayComparisonRow

_ACTION_LABELS = {
    MiddayActionKind.NEW: "New pick",
    MiddayActionKind.PENDING_CALIBRATE: "Pending order — calibrate on place",
    MiddayActionKind.OPEN_CALIBRATE: "Open position — calibrate targets on place",
}


def _fmt_delta(old: float | None, new: float) -> str:
    if old is None:
        return "—"
    delta = new - old
    if abs(delta) < 0.005:
        return "unchanged"
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta:.2f}"


def midday_comparison_dataframe(rows: list[MiddayComparisonRow]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()

    data = []
    for row in rows:
        data.append(
            {
                "Symbol": row.symbol,
                "Action": _ACTION_LABELS.get(row.action, row.action.value),
                "Plan status": row.plan_status or "—",
                "Shares": row.shares,
                "Pattern": row.pattern_name,
                "Morning buy": row.morning_buy,
                "Midday buy": row.midday_buy,
                "Buy Δ": _fmt_delta(row.morning_buy, row.midday_buy),
                "Morning target": row.morning_target,
                "Midday target": row.midday_target,
                "Target Δ": _fmt_delta(row.morning_target, row.midday_target),
                "Morning stop": row.morning_stop,
                "Midday stop": row.midday_stop,
                "Stop Δ": _fmt_delta(row.morning_stop, row.midday_stop),
            }
        )
    return pd.DataFrame(data)

"""Format backtest day results and bullish summary matrix."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

BACKEND_DIR = Path(__file__).resolve().parent.parent
UNIVERSE_PATH = BACKEND_DIR / "app" / "data" / "backtest_universe.json"


def _day_fields(d):
    if hasattr(d, "prev_close"):
        return {
            "trade_date": d.trade_date if isinstance(d.trade_date, date) else date.fromisoformat(str(d.trade_date)),
            "symbol": d.symbol,
            "signal": d.signal.value if hasattr(d.signal, "value") else d.signal,
            "actual": d.actual.value if hasattr(d.actual, "value") else d.actual,
            "correct": d.correct,
            "prev_close": float(d.prev_close),
            "predicted_close": float(d.predicted_close),
            "actual_close": float(d.actual_close),
        }
    return {
        "trade_date": date.fromisoformat(d["trade_date"]),
        "symbol": d["symbol"],
        "signal": d["signal"],
        "actual": d["actual"],
        "correct": d["correct"],
        "prev_close": float(d["prev_close"]),
        "predicted_close": float(d["predicted_close"]),
        "actual_close": float(d["actual_close"]),
    }


def _pct_off(predicted: float, actual: float) -> float:
    if predicted == 0:
        return 0.0
    return (actual - predicted) / predicted * 100


def _short_pattern_name(name: str) -> str:
    return name.replace(" (20,2)", "").replace(" (12,26,9)", "").replace(" (5/20)", "").replace(" (14)", "")[:28]


def _collect_latest_day_results(report) -> tuple[date | None, dict[tuple[str, str], dict]]:
    """Map (symbol, pattern_id) -> day fields for the latest eval date."""
    if hasattr(report, "prediction_date"):
        latest = report.prediction_date
        indexed: dict[tuple[str, str], dict] = {}
        for pr in report.patterns:
            for d in pr.day_details:
                fields = _day_fields(d)
                indexed[(fields["symbol"], pr.pattern_id)] = {**fields, "pattern_name": pr.pattern_name}
        return latest, indexed

    latest: date | None = None
    indexed: dict[tuple[str, str], dict] = {}

    for pr in report.patterns:
        for d in pr.day_details:
            fields = _day_fields(d)
            td = fields["trade_date"]
            if latest is None or td > latest:
                latest = td
    if latest is None:
        return None, {}

    for pr in report.patterns:
        for d in pr.day_details:
            fields = _day_fields(d)
            if fields["trade_date"] != latest:
                continue
            indexed[(fields["symbol"], pr.pattern_id)] = {**fields, "pattern_name": pr.pattern_name}

    return latest, indexed


def build_bullish_summary_matrix(
    report, symbols: list[str] | None = None
) -> tuple[pd.DataFrame, pd.DataFrame, date | None]:
    """
    Build stock × pattern matrix for latest day (bullish signals only).
    Returns (display_df, style_meta_df, eval_date).
    style_meta_df holds: match (bool|None), pct_off, signal, actual for styling.
    """
    if symbols is None:
        if hasattr(report, "symbols") and report.symbols:
            symbols = report.symbols
        else:
            symbols = json.loads(UNIVERSE_PATH.read_text())["symbols"]
    latest, indexed = _collect_latest_day_results(report)
    if latest is None:
        return pd.DataFrame(), pd.DataFrame(), None

    patterns = [(pr.pattern_id, _short_pattern_name(pr.pattern_name)) for pr in report.patterns]

    display_rows = []
    meta_rows = []

    for symbol in symbols:
        # Actual close for this stock on latest day (from any pattern row)
        actual_close = None
        actual_dir = None
        actual_chg = None
        for (sym, _), fields in indexed.items():
            if sym == symbol:
                actual_close = fields["actual_close"]
                actual_dir = fields["actual"]
                prev = fields["prev_close"]
                actual_chg = ((actual_close - prev) / prev * 100) if prev else 0
                break

        row = {
            "Stock": symbol,
            "Actual close": f"₹{actual_close:,.2f}" if actual_close else "—",
            "Actual move": f"{actual_chg:+.2f}%" if actual_chg is not None else "—",
            "Actual direction": actual_dir or "—",
        }
        meta = {"Stock": symbol}

        for pid, pname in patterns:
            fields = indexed.get((symbol, pid))
            if not fields or fields["signal"] != "BULLISH":
                row[pname] = "—"
                meta[pname] = {"match": None, "pct_off": None, "signal": None, "actual": actual_dir}
                continue

            pred = fields["predicted_close"]
            act = fields["actual_close"]
            off = _pct_off(pred, act)
            matched = fields["correct"]
            actual_label = fields["actual"]

            if matched:
                status = "✓ matched"
            elif fields["signal"] == "BULLISH" and actual_label == "BEARISH":
                status = "✗ bullish → bearish"
            elif fields["signal"] == "BEARISH" and actual_label == "BULLISH":
                status = "✗ bearish → bullish"
            else:
                status = "✗ mismatch"

            row[pname] = (
                f"Pred ₹{pred:,.2f}\n"
                f"Act ₹{act:,.2f}\n"
                f"Off {off:+.2f}%\n"
                f"{status}"
            )
            meta[pname] = {
                "match": matched,
                "pct_off": off,
                "signal": fields["signal"],
                "actual": actual_label,
            }

        display_rows.append(row)
        meta_rows.append(meta)

    return pd.DataFrame(display_rows), pd.DataFrame(meta_rows), latest


def prediction_context(report) -> tuple[date | None, date | None, int | None]:
    """Return (prediction_date, data_through_date, lookback_days) when available."""
    if report is None:
        return None, None, None
    if hasattr(report, "prediction_date"):
        return report.prediction_date, report.data_through_date, report.lookback_days
    latest, _ = _collect_latest_day_results(report)
    if latest is None:
        return None, None, None
    return latest, None, getattr(report, "lookback_days", None)


def build_validation_scorecard(report) -> dict:
    """Aggregate hit/miss counts for the prediction day (all non-neutral signals)."""
    _, indexed = _collect_latest_day_results(report)
    bullish_hits = bullish_total = 0
    bearish_hits = bearish_total = 0
    pattern_rows = []

    by_pattern: dict[str, dict] = {}
    for (symbol, pid), fields in indexed.items():
        sig = fields["signal"]
        if sig == "BULLISH":
            bullish_total += 1
            if fields["correct"]:
                bullish_hits += 1
        elif sig == "BEARISH":
            bearish_total += 1
            if fields["correct"]:
                bearish_hits += 1

        pname = fields.get("pattern_name", pid)
        if pname not in by_pattern:
            by_pattern[pname] = {"correct": 0, "signals": 0}
        by_pattern[pname]["signals"] += 1
        if fields["correct"]:
            by_pattern[pname]["correct"] += 1

    for pname, stats in sorted(by_pattern.items(), key=lambda x: (-x[1]["correct"], x[0])):
        hit = (stats["correct"] / stats["signals"] * 100) if stats["signals"] else 0
        pattern_rows.append(
            {
                "Pattern": pname,
                "Correct": stats["correct"],
                "Signals": stats["signals"],
                "Hit rate %": round(hit, 1),
            }
        )

    total = bullish_total + bearish_total
    hits = bullish_hits + bearish_hits
    return {
        "bullish_hits": bullish_hits,
        "bullish_total": bullish_total,
        "bearish_hits": bearish_hits,
        "bearish_total": bearish_total,
        "total_hits": hits,
        "total_signals": total,
        "overall_hit_rate": round(hits / total * 100, 1) if total else 0.0,
        "pattern_breakdown": pd.DataFrame(pattern_rows),
    }


def build_bearish_mismatch_summary(report) -> pd.DataFrame:
    """Latest-day bearish predictions for vice-versa visibility."""
    latest, indexed = _collect_latest_day_results(report)
    if latest is None:
        return pd.DataFrame()

    rows = []
    for pr in report.patterns:
        for d in pr.day_details:
            fields = _day_fields(d)
            if fields["trade_date"] != latest or fields["signal"] != "BEARISH":
                continue
            pred = fields["predicted_close"]
            act = fields["actual_close"]
            off = _pct_off(pred, act)
            rows.append(
                {
                    "Stock": fields["symbol"],
                    "Pattern": _short_pattern_name(pr.pattern_name),
                    "Predicted": f"₹{pred:,.2f}",
                    "Actual close": f"₹{act:,.2f}",
                    "Off %": f"{off:+.2f}%",
                    "Predicted dir": "BEARISH",
                    "Actual dir": fields["actual"],
                    "Result": "✓" if fields["correct"] else "✗ bearish → bullish",
                }
            )
    return pd.DataFrame(rows)


def style_bullish_summary(display_df: pd.DataFrame, meta_df: pd.DataFrame) -> pd.io.formats.style.Styler:
    """Green when bullish prediction matched actual; red on mismatch; neutral for empty."""

    def _row_style(row):
        styles = []
        for col in display_df.columns:
            if col in ("Stock", "Actual close", "Actual move", "Actual direction"):
                styles.append("")
                continue
            idx = row.name
            if col not in meta_df.columns:
                styles.append("")
                continue
            cell = meta_df.at[idx, col]
            if not isinstance(cell, dict) or cell.get("match") is None:
                styles.append("color: #888;")
                continue
            if cell["match"]:
                styles.append("background-color: #d4edda; color: #155724; font-weight: 600;")
            else:
                styles.append("background-color: #f8d7da; color: #721c24; font-weight: 600;")
        return styles

    return display_df.style.apply(_row_style, axis=1)


def pattern_detail_from_result(pr) -> dict:
    """Build pattern detail dict from an in-memory PatternResult (no DB re-run)."""
    symbols = sorted(set(pr.stock_signals.keys()) | set(pr.stock_correct.keys()))
    stock_scores = []
    for symbol in symbols:
        signals = int(pr.stock_signals.get(symbol, 0))
        correct = int(pr.stock_correct.get(symbol, 0))
        hit_rate = round(correct / signals * 100, 1) if signals else 0.0
        stock_scores.append(
            {
                "symbol": symbol,
                "correct": correct,
                "signals": signals,
                "hit_rate": hit_rate,
            }
        )
    return {
        "pattern_id": pr.pattern_id,
        "pattern_name": pr.pattern_name,
        "stock_scores": stock_scores,
        "day_details": pr.day_details,
    }


def day_details_dataframe(details) -> pd.DataFrame:
    """Format pattern day results with predicted vs actual prices."""
    rows = []
    for d in details:
        try:
            fields = _day_fields(d)
        except (KeyError, TypeError, ValueError):
            continue

        prev = fields["prev_close"]
        pred = fields["predicted_close"]
        act = fields["actual_close"]
        pred_chg = ((pred - prev) / prev * 100) if prev else 0
        act_chg = ((act - prev) / prev * 100) if prev else 0
        rows.append(
            {
                "Date": fields["trade_date"].isoformat(),
                "Symbol": fields["symbol"],
                "Signal": fields["signal"],
                "Prev close": f"₹{prev:,.2f}",
                "Predicted close": f"₹{pred:,.2f}",
                "Actual close": f"₹{act:,.2f}",
                "Predicted Δ%": f"{pred_chg:+.2f}%",
                "Actual Δ%": f"{act_chg:+.2f}%",
                "Price off %": f"{_pct_off(pred, act):+.2f}%",
                "Actual direction": fields["actual"],
                "Match": "✓" if fields["correct"] else "✗",
            }
        )
    return pd.DataFrame(rows)

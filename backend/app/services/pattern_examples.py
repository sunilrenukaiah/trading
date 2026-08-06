"""Synthetic OHLCV examples that illustrate each registered pattern."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

# (open, high, low, close) for pattern-forming candles at the end of the series.
PATTERN_TAIL_OHLC: dict[str, list[tuple[float, float, float, float]]] = {
    "cs_morning_star": [(98, 99, 93, 94), (94.5, 95, 94, 94.8), (95, 99, 94.5, 98)],
    "cs_evening_star": [(92, 94, 91.5, 93.5), (93.6, 94, 93.4, 93.5), (93.4, 93.8, 90, 90.5)],
    "cs_piercing_line": [(98, 99, 96, 96.5), (96, 98.5, 95.5, 98)],
    "cs_dark_cloud_cover": [(92, 94, 91.5, 93.5), (94, 94.5, 91, 92)],
    "cs_bullish_harami": [(98, 99, 94, 95), (95.2, 96, 94.8, 95.8)],
    "cs_bearish_harami": [(92, 94, 91.5, 93.5), (93.2, 93.8, 92.5, 92.8)],
    "cs_three_white_soldiers": [(94, 95, 93.5, 94.8), (94.7, 96, 94.5, 95.8), (95.6, 97, 95.4, 96.8)],
    "cs_three_black_crows": [(98, 99, 96, 96.5), (96.4, 97, 94.5, 95), (94.9, 95.5, 93, 93.5)],
    "cs_inverted_hammer": [(100.5, 101, 100, 100.8)],
    "cs_shooting_star": [(89.5, 90, 89.2, 89.8)],
    "cs_hanging_man": [(90.5, 91, 90.2, 90.8)],
    "cs_dragonfly_doji": [(94.5, 94.55, 92, 94.52)],
    "cs_bullish_abandoned_baby": [(98, 99, 95, 96), (93.5, 93.6, 93.4, 93.45), (94.5, 96, 94.4, 95.5)],
    "cs_bearish_abandoned_baby": [(92, 94, 91.5, 93.5), (94.2, 94.3, 94.1, 94.15), (93.5, 93.8, 91.5, 92)],
    "cs_three_inside_up": [(98, 99, 94, 95), (95.2, 96, 94.8, 95.8), (96, 98, 95.8, 97.5)],
    "cs_three_inside_down": [(92, 94, 91.5, 93.5), (93.2, 93.8, 92.5, 92.8), (92.5, 93, 91, 91.5)],
    "cs_three_outside_up": [(98, 99, 96, 96.5), (96, 99.5, 95.5, 99), (98.5, 100, 98, 99.5)],
    "cs_three_outside_down": [(92, 94, 91.5, 93.5), (93.5, 94, 91, 91.5), (91.2, 91.8, 90, 90.5)],
    "cs_bullish_kicker": [(98, 99, 96, 96.5), (97, 100, 96.5, 99.5)],
    "cs_bearish_kicker": [(92, 94, 91.5, 93.5), (93, 93.5, 90, 90.5)],
    "cs_tweezer_bottom": [(98, 99, 94, 95), (95, 96, 94, 95.5)],
    "cs_tweezer_top": [(92, 94, 91.5, 93.5), (93.5, 94, 92, 92.5)],
    "cs_rising_three_methods": [
        (88, 90, 87.5, 89.5),
        (89.3, 89.8, 88.5, 88.8),
        (88.7, 89.2, 88.2, 88.5),
        (88.4, 88.9, 88, 88.3),
        (88.5, 91, 88.3, 90.5),
    ],
    "cs_bearish_mat_hold": [
        (98, 99, 96, 96.5),
        (96.4, 97, 95.8, 96.2),
        (96.1, 96.6, 95.5, 95.9),
        (95.8, 96.3, 95.2, 95.6),
        (95.5, 96, 93, 93.5),
    ],
    "cs_bullish_mat_hold": [
        (88, 90, 87.5, 89.5),
        (89.3, 89.8, 88.5, 88.8),
        (88.7, 89.2, 88.2, 88.5),
        (88.4, 88.9, 88, 88.3),
        (88.5, 91, 88.3, 90.5),
    ],
    "cs_concealing_baby_swallow": [
        (98, 99, 95, 96),
        (95.5, 96, 93, 94),
        (92.5, 93, 91.5, 92),
        (91, 93.5, 90.5, 93),
    ],
    "cs_bullish_separating_lines": [(89.5, 90, 89, 89.3), (89.5, 91, 89.4, 90.5)],
    "cs_bullish_belt_hold": [(94.5, 96, 94.45, 95.8)],
    "cs_bearish_belt_hold": [(90.5, 90.55, 89, 89.2)],
    "cs_bullish_three_line_strike": [
        (88, 89, 87.5, 88.8),
        (88.7, 90, 88.5, 89.8),
        (89.6, 91, 89.4, 90.8),
        (91, 91.5, 87.5, 88),
    ],
    "cs_bearish_three_line_strike": [
        (98, 99, 96, 98.5),
        (98.3, 100, 98, 99.5),
        (99.2, 101, 99, 100.5),
        (100.5, 101, 98, 99),
    ],
    "cs_ladder_bottom": [
        (98, 99, 96, 96.5),
        (96.4, 97, 94.5, 95),
        (94.9, 95.5, 93, 93.5),
        (93.4, 94, 92, 92.5),
        (92.4, 94, 92, 93.5),
    ],
    "cs_meeting_lines": [(98, 99, 96, 96.5), (96, 97, 95.5, 96.4)],
    "cs_bearish_doji_star": [(92, 94, 91.5, 93.5), (93.6, 94, 93.4, 93.45)],
    "cs_upside_gap_two_crows": [(88, 90, 87.5, 89.5), (90, 90.5, 89.5, 90.2), (90.1, 90.4, 89, 89.3)],
    "p10_doji": [(94.5, 95, 94, 94.48)],
    "p11_hammer": [(94.5, 95, 92, 94.8)],
    "p7_engulfing": [(98, 99, 96, 96.5), (96, 99, 95.5, 98.5)],
}

PATTERN_CONTEXT: dict[str, str] = {
    "cs_evening_star": "uptrend",
    "cs_dark_cloud_cover": "uptrend",
    "cs_bearish_harami": "uptrend",
    "cs_shooting_star": "uptrend",
    "cs_hanging_man": "uptrend",
    "cs_bearish_abandoned_baby": "uptrend",
    "cs_three_inside_down": "uptrend",
    "cs_three_outside_down": "uptrend",
    "cs_bearish_kicker": "uptrend",
    "cs_tweezer_top": "uptrend",
    "cs_bearish_mat_hold": "downtrend",
    "cs_bearish_belt_hold": "uptrend",
    "cs_bearish_three_line_strike": "uptrend",
    "cs_bearish_doji_star": "uptrend",
    "cs_rising_three_methods": "uptrend",
    "cs_bullish_mat_hold": "uptrend",
    "cs_bullish_three_line_strike": "uptrend",
    "cs_bullish_separating_lines": "uptrend",
    "cs_upside_gap_two_crows": "uptrend",
    "p8_sma_trend": "uptrend",
    "p1_sma_cross": "cross_up",
    "p2_rsi_momentum": "rsi_up",
    "p3_macd_cross": "macd_up",
    "p4_bb_mean_reversion": "bb_lower",
    "p5_bb_breakout": "bb_break_up",
    "p6_bb_squeeze": "bb_squeeze",
    "p9_swing_structure": "swing_up",
    "p12_volume_breakout": "vol_break",
}


def _context_closes(kind: str, n: int) -> list[float]:
    if kind == "uptrend":
        return [80 + i * 0.55 for i in range(n)]
    if kind == "cross_up":
        base = [100 - i * 0.15 for i in range(n - 3)]
        return base + [99.2, 99.8, 100.6]
    if kind == "rsi_up":
        base = [98 - i * 0.1 for i in range(n - 4)]
        return base + [97.8, 98.2, 98.8, 99.5]
    if kind == "macd_up":
        return [95 + i * 0.12 for i in range(n)]
    if kind == "bb_lower":
        return [105 - i * 0.45 for i in range(n)]
    if kind == "bb_break_up":
        base = [100 + i * 0.05 for i in range(n - 2)]
        return base + [100.8, 102.5]
    if kind == "bb_squeeze":
        mid = 100.0
        return [mid + (0.08 if i % 2 == 0 else -0.08) for i in range(n - 1)] + [101.2]
    if kind == "swing_up":
        return [90 + i * 0.35 for i in range(n)]
    if kind == "vol_break":
        base = [100 + (0.02 if i < n - 1 else 0.8) for i in range(n)]
        return base
    # downtrend default
    return [110 - i * 0.65 for i in range(n)]


def _closes_to_ohlc(closes: list[float]) -> list[tuple[float, float, float, float]]:
    rows: list[tuple[float, float, float, float]] = []
    for i, close in enumerate(closes):
        open_ = closes[i - 1] if i else close + 0.2
        high = max(open_, close) + 0.4
        low = min(open_, close) - 0.4
        rows.append((open_, high, low, close))
    return rows


def build_technical_example(pattern_id: str, bars: int = 25) -> pd.DataFrame:
    kind = PATTERN_CONTEXT.get(pattern_id, "downtrend")
    closes = _context_closes(kind, bars)
    if pattern_id == "p12_volume_breakout":
        ohlc = _closes_to_ohlc(closes)
        rows = []
        start = date(2026, 1, 2)
        for i, (o, h, l, c) in enumerate(ohlc):
            vol = 25000 if i == len(ohlc) - 1 else 8000
            rows.append(
                {
                    "trade_date": start + timedelta(days=i),
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "volume": vol,
                }
            )
        return pd.DataFrame(rows)
    return _ohlc_to_df(_closes_to_ohlc(closes))


def _ohlc_to_df(ohlc: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    start = date(2026, 1, 2)
    rows = []
    for i, (o, h, l, c) in enumerate(ohlc):
        rows.append(
            {
                "trade_date": start + timedelta(days=i),
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": 10000,
            }
        )
    return pd.DataFrame(rows)


def build_pattern_example(pattern_id: str, *, min_bars: int = 25) -> tuple[pd.DataFrame, int]:
    """Return OHLCV dataframe and number of trailing bars to highlight."""
    if pattern_id in PATTERN_TAIL_OHLC:
        tail = PATTERN_TAIL_OHLC[pattern_id]
        context_kind = PATTERN_CONTEXT.get(pattern_id, "downtrend")
        ctx_len = max(min_bars - len(tail), 18)
        ctx = _closes_to_ohlc(_context_closes(context_kind, ctx_len))
        df = _ohlc_to_df(ctx + tail)
        return df, len(tail)

    if pattern_id.startswith(("p", "c")):
        df = build_technical_example(pattern_id)
        span = 3 if pattern_id in {"p1_sma_cross", "p3_macd_cross", "c1_sma_cross_rsi"} else 5
        return df, min(span, len(df))

    # Combination patterns — reuse primary component chart
    combo_map = {
        "c1_sma_cross_rsi": "p1_sma_cross",
        "c2_macd_sma_trend": "p3_macd_cross",
        "c3_engulfing_volume": "p7_engulfing",
        "c4_hammer_bb": "p11_hammer",
        "c5_doji_bb": "p10_doji",
    }
    if pattern_id in combo_map:
        df, span = build_pattern_example(combo_map[pattern_id], min_bars=min_bars)
        return df, span

    ctx = _closes_to_ohlc(_context_closes("downtrend", min_bars))
    return _ohlc_to_df(ctx), 3

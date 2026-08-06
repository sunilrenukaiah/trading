"""Classic chart patterns: inside bar, NR4, double tops/bottoms, flags, triangles, wedges."""

from __future__ import annotations

import pandas as pd

from app.strategies.base import Signal
from app.strategies.indicators import candle_range, sma
from app.strategies.registry import register_pattern

PEAK_TOLERANCE = 0.012


def _in_uptrend(candles: pd.DataFrame) -> bool:
    if len(candles) < 20:
        return False
    close = candles["close"].astype(float)
    sma20 = sma(close, 20)
    return not pd.isna(sma20.iloc[-1]) and close.iloc[-1] > sma20.iloc[-1]


def _in_downtrend(candles: pd.DataFrame) -> bool:
    if len(candles) < 20:
        return False
    close = candles["close"].astype(float)
    sma20 = sma(close, 20)
    return not pd.isna(sma20.iloc[-1]) and close.iloc[-1] < sma20.iloc[-1]


def _near(a: float, b: float, tol: float = PEAK_TOLERANCE) -> bool:
    base = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / base <= tol


def _local_peaks(highs: pd.Series, window: int = 2) -> list[tuple[int, float]]:
    peaks: list[tuple[int, float]] = []
    for i in range(window, len(highs) - window):
        h = float(highs.iloc[i])
        if all(h >= float(highs.iloc[i - j]) for j in range(1, window + 1)):
            if all(h >= float(highs.iloc[i + j]) for j in range(1, window + 1)):
                peaks.append((i, h))
    return peaks


def _local_troughs(lows: pd.Series, window: int = 2) -> list[tuple[int, float]]:
    troughs: list[tuple[int, float]] = []
    for i in range(window, len(lows) - window):
        lv = float(lows.iloc[i])
        if all(lv <= float(lows.iloc[i - j]) for j in range(1, window + 1)):
            if all(lv <= float(lows.iloc[i + j]) for j in range(1, window + 1)):
                troughs.append((i, lv))
    return troughs


@register_pattern
class InsideBarPattern:
    id = "pa_inside_bar"
    name = "Inside Bar"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 2:
            return Signal.NEUTRAL
        prev, last = candles.iloc[-2], candles.iloc[-1]
        if float(last["high"]) >= float(prev["high"]) or float(last["low"]) <= float(prev["low"]):
            return Signal.NEUTRAL
        mid = (float(last["high"]) + float(last["low"])) / 2
        if float(last["close"]) > mid and _in_uptrend(candles):
            return Signal.BULLISH
        if float(last["close"]) < mid and _in_downtrend(candles):
            return Signal.BEARISH
        return Signal.NEUTRAL


@register_pattern
class NR4Pattern:
    id = "pa_nr4"
    name = "NR4 (Narrow Range 4)"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 4:
            return Signal.NEUTRAL
        tail = candles.tail(4)
        ranges = [candle_range(tail.iloc[i]) for i in range(4)]
        if ranges[-1] > min(ranges):
            return Signal.NEUTRAL
        last = tail.iloc[-1]
        o, c = float(last["open"]), float(last["close"])
        if c > o:
            return Signal.BULLISH
        if c < o:
            return Signal.BEARISH
        return Signal.NEUTRAL


@register_pattern
class BreakoutFailurePattern:
    id = "pa_breakout_failure"
    name = "Breakout Failure"
    lookback_days = 25

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 22:
            return Signal.NEUTRAL
        prior = candles.iloc[:-2]
        breakout = candles.iloc[-2]
        last = candles.iloc[-1]
        high_20 = prior["high"].astype(float).tail(20).max()
        low_20 = prior["low"].astype(float).tail(20).min()
        if float(breakout["high"]) > high_20 and float(last["close"]) < high_20:
            return Signal.BEARISH
        if float(breakout["low"]) < low_20 and float(last["close"]) > low_20:
            return Signal.BULLISH
        return Signal.NEUTRAL


@register_pattern
class DoubleTopPattern:
    id = "pa_double_top"
    name = "Double Top"
    lookback_days = 30

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 25:
            return Signal.NEUTRAL
        window = candles.tail(25)
        highs = window["high"].astype(float)
        peaks = _local_peaks(highs)
        if len(peaks) < 2:
            return Signal.NEUTRAL
        p1_idx, p1 = peaks[-2]
        p2_idx, p2 = peaks[-1]
        if p2_idx - p1_idx < 3 or not _near(p1, p2):
            return Signal.NEUTRAL
        neckline = float(window["low"].iloc[p1_idx:p2_idx].min())
        if float(window["close"].iloc[-1]) < neckline and _in_uptrend(candles):
            return Signal.BEARISH
        return Signal.NEUTRAL


@register_pattern
class DoubleBottomPattern:
    id = "pa_double_bottom"
    name = "Double Bottom"
    lookback_days = 30

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 25:
            return Signal.NEUTRAL
        window = candles.tail(25)
        lows = window["low"].astype(float)
        troughs = _local_troughs(lows)
        if len(troughs) < 2:
            return Signal.NEUTRAL
        t1_idx, t1 = troughs[-2]
        t2_idx, t2 = troughs[-1]
        if t2_idx - t1_idx < 3 or not _near(t1, t2):
            return Signal.NEUTRAL
        neckline = float(window["high"].iloc[t1_idx:t2_idx].max())
        if float(window["close"].iloc[-1]) > neckline and _in_downtrend(candles):
            return Signal.BULLISH
        return Signal.NEUTRAL


@register_pattern
class HeadShouldersPattern:
    id = "pa_head_shoulders"
    name = "Head and Shoulders"
    lookback_days = 35

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 30:
            return Signal.NEUTRAL
        window = candles.tail(30)
        highs = window["high"].astype(float)
        peaks = _local_peaks(highs)
        if len(peaks) < 3:
            return Signal.NEUTRAL
        ls_idx, ls = peaks[-3]
        head_idx, head = peaks[-2]
        rs_idx, rs = peaks[-1]
        if not (head > ls and head > rs and _near(ls, rs)):
            return Signal.NEUTRAL
        neckline = float(window["low"].iloc[ls_idx:rs_idx].min())
        if float(window["close"].iloc[-1]) < neckline:
            return Signal.BEARISH
        return Signal.NEUTRAL


@register_pattern
class InverseHeadShouldersPattern:
    id = "pa_inverse_head_shoulders"
    name = "Inverse Head and Shoulders"
    lookback_days = 35

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 30:
            return Signal.NEUTRAL
        window = candles.tail(30)
        lows = window["low"].astype(float)
        troughs = _local_troughs(lows)
        if len(troughs) < 3:
            return Signal.NEUTRAL
        ls_idx, ls = troughs[-3]
        head_idx, head = troughs[-2]
        rs_idx, rs = troughs[-1]
        if not (head < ls and head < rs and _near(ls, rs)):
            return Signal.NEUTRAL
        neckline = float(window["high"].iloc[ls_idx:rs_idx].max())
        if float(window["close"].iloc[-1]) > neckline:
            return Signal.BULLISH
        return Signal.NEUTRAL


@register_pattern
class CupHandlePattern:
    id = "pa_cup_handle"
    name = "Cup and Handle"
    lookback_days = 40

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 35:
            return Signal.NEUTRAL
        window = candles.tail(35)
        close = window["close"].astype(float)
        left_rim = float(close.iloc[0])
        cup_low = float(close.iloc[5:25].min())
        right_rim = float(close.iloc[24])
        handle_low = float(close.iloc[25:].min())
        last = float(close.iloc[-1])
        cup_depth = (left_rim - cup_low) / max(left_rim, 1e-9)
        if cup_depth < 0.08 or cup_depth > 0.35:
            return Signal.NEUTRAL
        if not _near(left_rim, right_rim, 0.03):
            return Signal.NEUTRAL
        if handle_low < cup_low or handle_low > right_rim:
            return Signal.NEUTRAL
        if last > right_rim * 1.01:
            return Signal.BULLISH
        return Signal.NEUTRAL


@register_pattern
class BullFlagPattern:
    id = "pa_bull_flag"
    name = "Bull Flag"
    lookback_days = 25

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 20:
            return Signal.NEUTRAL
        window = candles.tail(20)
        close = window["close"].astype(float)
        pole_gain = (close.iloc[9] - close.iloc[0]) / max(close.iloc[0], 1e-9)
        if pole_gain < 0.05:
            return Signal.NEUTRAL
        flag = window.iloc[10:]
        flag_slope = (float(flag["close"].iloc[-1]) - float(flag["close"].iloc[0])) / max(
            float(flag["close"].iloc[0]), 1e-9
        )
        if flag_slope > 0.02:
            return Signal.NEUTRAL
        if float(close.iloc[-1]) > float(flag["high"].max()):
            return Signal.BULLISH
        return Signal.NEUTRAL


@register_pattern
class BearFlagPattern:
    id = "pa_bear_flag"
    name = "Bear Flag"
    lookback_days = 25

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 20:
            return Signal.NEUTRAL
        window = candles.tail(20)
        close = window["close"].astype(float)
        pole_drop = (close.iloc[0] - close.iloc[9]) / max(close.iloc[0], 1e-9)
        if pole_drop < 0.05:
            return Signal.NEUTRAL
        flag = window.iloc[10:]
        flag_slope = (float(flag["close"].iloc[-1]) - float(flag["close"].iloc[0])) / max(
            float(flag["close"].iloc[0]), 1e-9
        )
        if flag_slope < -0.02:
            return Signal.NEUTRAL
        if float(close.iloc[-1]) < float(flag["low"].min()):
            return Signal.BEARISH
        return Signal.NEUTRAL


@register_pattern
class AscendingTrianglePattern:
    id = "pa_ascending_triangle"
    name = "Ascending Triangle"
    lookback_days = 25

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 20:
            return Signal.NEUTRAL
        window = candles.tail(20)
        highs = window["high"].astype(float)
        lows = window["low"].astype(float)
        flat_top = highs.max()
        if (highs.max() - highs.min()) / max(flat_top, 1e-9) > 0.015:
            return Signal.NEUTRAL
        rising_lows = all(lows.iloc[i] >= lows.iloc[i - 1] * 0.998 for i in range(1, len(lows)))
        if not rising_lows:
            return Signal.NEUTRAL
        if float(window["close"].iloc[-1]) > flat_top * 0.998:
            return Signal.BULLISH
        return Signal.NEUTRAL


@register_pattern
class DescendingTrianglePattern:
    id = "pa_descending_triangle"
    name = "Descending Triangle"
    lookback_days = 25

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 20:
            return Signal.NEUTRAL
        window = candles.tail(20)
        highs = window["high"].astype(float)
        lows = window["low"].astype(float)
        flat_bottom = lows.min()
        if (lows.max() - lows.min()) / max(flat_bottom, 1e-9) > 0.015:
            return Signal.NEUTRAL
        falling_highs = all(highs.iloc[i] <= highs.iloc[i - 1] * 1.002 for i in range(1, len(highs)))
        if not falling_highs:
            return Signal.NEUTRAL
        if float(window["close"].iloc[-1]) < flat_bottom * 1.002:
            return Signal.BEARISH
        return Signal.NEUTRAL


@register_pattern
class SymmetricalTrianglePattern:
    id = "pa_symmetrical_triangle"
    name = "Symmetrical Triangle"
    lookback_days = 25

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 20:
            return Signal.NEUTRAL
        window = candles.tail(20)
        highs = window["high"].astype(float)
        lows = window["low"].astype(float)
        early_range = float(highs.iloc[0]) - float(lows.iloc[0])
        late_range = float(highs.iloc[-1]) - float(lows.iloc[-1])
        if early_range <= 0 or late_range >= early_range * 0.6:
            return Signal.NEUTRAL
        falling_highs = float(highs.iloc[-1]) < float(highs.iloc[0])
        rising_lows = float(lows.iloc[-1]) > float(lows.iloc[0])
        if not (falling_highs and rising_lows):
            return Signal.NEUTRAL
        last_close = float(window["close"].iloc[-1])
        if last_close > float(highs.iloc[-2]):
            return Signal.BULLISH
        if last_close < float(lows.iloc[-2]):
            return Signal.BEARISH
        return Signal.NEUTRAL


@register_pattern
class RectanglePattern:
    id = "pa_rectangle"
    name = "Rectangle (Trading Range)"
    lookback_days = 25

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 20:
            return Signal.NEUTRAL
        window = candles.tail(20)
        highs = window["high"].astype(float)
        lows = window["low"].astype(float)
        top = highs.max()
        bottom = lows.min()
        mid = (top + bottom) / 2
        height_pct = (top - bottom) / max(mid, 1e-9)
        if height_pct > 0.08 or height_pct < 0.02:
            return Signal.NEUTRAL
        last_close = float(window["close"].iloc[-1])
        if last_close > top * 0.998:
            return Signal.BULLISH
        if last_close < bottom * 1.002:
            return Signal.BEARISH
        return Signal.NEUTRAL


@register_pattern
class RisingWedgePattern:
    id = "pa_rising_wedge"
    name = "Rising Wedge"
    lookback_days = 25

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 20:
            return Signal.NEUTRAL
        window = candles.tail(20)
        highs = window["high"].astype(float)
        lows = window["low"].astype(float)
        if float(highs.iloc[-1]) <= float(highs.iloc[0]):
            return Signal.NEUTRAL
        if float(lows.iloc[-1]) <= float(lows.iloc[0]):
            return Signal.NEUTRAL
        early = float(highs.iloc[0]) - float(lows.iloc[0])
        late = float(highs.iloc[-1]) - float(lows.iloc[-1])
        if late >= early * 0.85:
            return Signal.NEUTRAL
        if float(window["close"].iloc[-1]) < float(lows.iloc[-2]):
            return Signal.BEARISH
        return Signal.NEUTRAL


@register_pattern
class FallingWedgePattern:
    id = "pa_falling_wedge"
    name = "Falling Wedge"
    lookback_days = 25

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 20:
            return Signal.NEUTRAL
        window = candles.tail(20)
        highs = window["high"].astype(float)
        lows = window["low"].astype(float)
        if float(highs.iloc[-1]) >= float(highs.iloc[0]):
            return Signal.NEUTRAL
        if float(lows.iloc[-1]) >= float(lows.iloc[0]):
            return Signal.NEUTRAL
        early = float(highs.iloc[0]) - float(lows.iloc[0])
        late = float(highs.iloc[-1]) - float(lows.iloc[-1])
        if late >= early * 0.85:
            return Signal.NEUTRAL
        if float(window["close"].iloc[-1]) > float(highs.iloc[-2]):
            return Signal.BULLISH
        return Signal.NEUTRAL

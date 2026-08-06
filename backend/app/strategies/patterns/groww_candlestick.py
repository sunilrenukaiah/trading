"""Candlestick patterns from Groww's 38-pattern guide.

Reference: https://groww.in/blog/candlestick-patterns
"""

from __future__ import annotations

import pandas as pd

from app.strategies.base import Signal
from app.strategies.indicators import candle_body, candle_range, lower_wick, sma, upper_wick
from app.strategies.registry import register_pattern

DOJI_BODY_RATIO = 0.1
LONG_BODY_RATIO = 0.55
SMALL_BODY_RATIO = 0.35


def _ohlc(row) -> tuple[float, float, float, float]:
    return float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])


def _is_bullish(row) -> bool:
    o, _, _, c = _ohlc(row)
    return c > o


def _is_bearish(row) -> bool:
    o, _, _, c = _ohlc(row)
    return c < o


def _body_top(row) -> float:
    o, _, _, c = _ohlc(row)
    return max(o, c)


def _body_bottom(row) -> float:
    o, _, _, c = _ohlc(row)
    return min(o, c)


def _body_mid(row) -> float:
    return (_body_top(row) + _body_bottom(row)) / 2


def _is_doji(row, max_body_ratio: float = DOJI_BODY_RATIO) -> bool:
    rng = candle_range(row)
    if rng <= 0:
        return False
    return candle_body(row) / rng <= max_body_ratio


def _is_long_body(row, min_ratio: float = LONG_BODY_RATIO) -> bool:
    rng = candle_range(row)
    if rng <= 0:
        return False
    return candle_body(row) / rng >= min_ratio


def _is_small_body(row, max_ratio: float = SMALL_BODY_RATIO) -> bool:
    rng = candle_range(row)
    if rng <= 0:
        return False
    return candle_body(row) / rng <= max_ratio


def _near(a: float, b: float, tol_pct: float = 0.004) -> bool:
    base = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / base <= tol_pct


def _gap_up(prev, curr) -> bool:
    return float(curr["low"]) > float(prev["high"])


def _gap_down(prev, curr) -> bool:
    return float(curr["high"]) < float(prev["low"])


def _body_inside(inner, outer) -> bool:
    return _body_top(inner) <= _body_top(outer) and _body_bottom(inner) >= _body_bottom(outer)


def _in_downtrend(candles: pd.DataFrame) -> bool:
    if len(candles) < 20:
        return False
    close = candles["close"].astype(float)
    sma20 = sma(close, 20)
    if pd.isna(sma20.iloc[-1]):
        return False
    return close.iloc[-1] < sma20.iloc[-1]


def _in_uptrend(candles: pd.DataFrame) -> bool:
    if len(candles) < 20:
        return False
    close = candles["close"].astype(float)
    sma20 = sma(close, 20)
    if pd.isna(sma20.iloc[-1]):
        return False
    return close.iloc[-1] > sma20.iloc[-1]


@register_pattern
class MorningStarPattern:
    id = "cs_morning_star"
    name = "Morning Star"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 3 or not _in_downtrend(candles):
            return Signal.NEUTRAL
        c1, c2, c3 = candles.iloc[-3], candles.iloc[-2], candles.iloc[-1]
        if not (_is_bearish(c1) and _is_long_body(c1)):
            return Signal.NEUTRAL
        if not _is_small_body(c2):
            return Signal.NEUTRAL
        if not (_is_bullish(c3) and _is_long_body(c3)):
            return Signal.NEUTRAL
        if _body_bottom(c3) <= _body_mid(c1):
            return Signal.NEUTRAL
        return Signal.BULLISH


@register_pattern
class EveningStarPattern:
    id = "cs_evening_star"
    name = "Evening Star"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 3 or not _in_uptrend(candles):
            return Signal.NEUTRAL
        c1, c2, c3 = candles.iloc[-3], candles.iloc[-2], candles.iloc[-1]
        if not (_is_bullish(c1) and _is_long_body(c1)):
            return Signal.NEUTRAL
        if not _is_small_body(c2):
            return Signal.NEUTRAL
        if not (_is_bearish(c3) and _is_long_body(c3)):
            return Signal.NEUTRAL
        if _body_top(c3) >= _body_mid(c1):
            return Signal.NEUTRAL
        return Signal.BEARISH


@register_pattern
class PiercingLinePattern:
    id = "cs_piercing_line"
    name = "Piercing Line"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 2 or not _in_downtrend(candles):
            return Signal.NEUTRAL
        c1, c2 = candles.iloc[-2], candles.iloc[-1]
        if not (_is_bearish(c1) and _is_bullish(c2)):
            return Signal.NEUTRAL
        _, _, _, c1_close = _ohlc(c1)
        c2_open, _, _, c2_close = _ohlc(c2)
        if c2_open >= c1_close:
            return Signal.NEUTRAL
        if c2_close <= _body_mid(c1):
            return Signal.NEUTRAL
        return Signal.BULLISH


@register_pattern
class DarkCloudCoverPattern:
    id = "cs_dark_cloud_cover"
    name = "Dark Cloud Cover"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 2 or not _in_uptrend(candles):
            return Signal.NEUTRAL
        c1, c2 = candles.iloc[-2], candles.iloc[-1]
        if not (_is_bullish(c1) and _is_bearish(c2)):
            return Signal.NEUTRAL
        c1_open, c1_high, _, _ = _ohlc(c1)
        c2_open, _, _, c2_close = _ohlc(c2)
        if c2_open <= c1_high:
            return Signal.NEUTRAL
        if c2_close >= _body_mid(c1):
            return Signal.NEUTRAL
        if c2_close <= c1_open:
            return Signal.NEUTRAL
        return Signal.BEARISH


@register_pattern
class BullishHaramiPattern:
    id = "cs_bullish_harami"
    name = "Bullish Harami"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 2 or not _in_downtrend(candles):
            return Signal.NEUTRAL
        c1, c2 = candles.iloc[-2], candles.iloc[-1]
        if not (_is_bearish(c1) and _is_long_body(c1) and _is_bullish(c2)):
            return Signal.NEUTRAL
        if not _body_inside(c2, c1):
            return Signal.NEUTRAL
        return Signal.BULLISH


@register_pattern
class BearishHaramiPattern:
    id = "cs_bearish_harami"
    name = "Bearish Harami"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 2 or not _in_uptrend(candles):
            return Signal.NEUTRAL
        c1, c2 = candles.iloc[-2], candles.iloc[-1]
        if not (_is_bullish(c1) and _is_long_body(c1) and _is_bearish(c2)):
            return Signal.NEUTRAL
        if not _body_inside(c2, c1):
            return Signal.NEUTRAL
        return Signal.BEARISH


@register_pattern
class ThreeWhiteSoldiersPattern:
    id = "cs_three_white_soldiers"
    name = "Three White Soldiers"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 3:
            return Signal.NEUTRAL
        c1, c2, c3 = candles.iloc[-3], candles.iloc[-2], candles.iloc[-1]
        for c in (c1, c2, c3):
            if not (_is_bullish(c) and _is_long_body(c)):
                return Signal.NEUTRAL
            body = candle_body(c)
            if upper_wick(c) > body or lower_wick(c) > body:
                return Signal.NEUTRAL
        if not (_body_top(c2) > _body_top(c1) and _body_top(c3) > _body_top(c2)):
            return Signal.NEUTRAL
        if _body_bottom(c2) < _body_bottom(c1) or _body_bottom(c3) < _body_bottom(c2):
            return Signal.NEUTRAL
        return Signal.BULLISH


@register_pattern
class ThreeBlackCrowsPattern:
    id = "cs_three_black_crows"
    name = "Three Black Crows"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 3:
            return Signal.NEUTRAL
        c1, c2, c3 = candles.iloc[-3], candles.iloc[-2], candles.iloc[-1]
        for c in (c1, c2, c3):
            if not (_is_bearish(c) and _is_long_body(c)):
                return Signal.NEUTRAL
            body = candle_body(c)
            if upper_wick(c) > body or lower_wick(c) > body:
                return Signal.NEUTRAL
        if not (_body_bottom(c2) < _body_bottom(c1) and _body_bottom(c3) < _body_bottom(c2)):
            return Signal.NEUTRAL
        return Signal.BEARISH


@register_pattern
class InvertedHammerPattern:
    id = "cs_inverted_hammer"
    name = "Inverted Hammer"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 20 or not _in_downtrend(candles):
            return Signal.NEUTRAL
        last = candles.iloc[-1]
        body = candle_body(last)
        if body <= 0:
            return Signal.NEUTRAL
        upper = upper_wick(last)
        lower = lower_wick(last)
        if upper >= 2 * body and lower <= body * 0.5:
            return Signal.BULLISH
        return Signal.NEUTRAL


@register_pattern
class ShootingStarPattern:
    id = "cs_shooting_star"
    name = "Shooting Star"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 20 or not _in_uptrend(candles):
            return Signal.NEUTRAL
        last = candles.iloc[-1]
        body = candle_body(last)
        if body <= 0:
            return Signal.NEUTRAL
        upper = upper_wick(last)
        lower = lower_wick(last)
        if upper >= 2 * body and lower <= body * 0.5:
            return Signal.BEARISH
        return Signal.NEUTRAL


@register_pattern
class HangingManPattern:
    id = "cs_hanging_man"
    name = "Hanging Man"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 20 or not _in_uptrend(candles):
            return Signal.NEUTRAL
        last = candles.iloc[-1]
        body = candle_body(last)
        if body <= 0:
            return Signal.NEUTRAL
        lower = lower_wick(last)
        upper = upper_wick(last)
        if lower >= 2 * body and upper <= body * 0.5:
            return Signal.BEARISH
        return Signal.NEUTRAL


@register_pattern
class DragonflyDojiPattern:
    id = "cs_dragonfly_doji"
    name = "Dragonfly Doji"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 20 or not _in_downtrend(candles):
            return Signal.NEUTRAL
        last = candles.iloc[-1]
        if not _is_doji(last):
            return Signal.NEUTRAL
        body = candle_body(last)
        if lower_wick(last) >= 2 * max(body, candle_range(last) * 0.01) and upper_wick(last) <= body:
            return Signal.BULLISH
        return Signal.NEUTRAL


@register_pattern
class BullishAbandonedBabyPattern:
    id = "cs_bullish_abandoned_baby"
    name = "Bullish Abandoned Baby"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 3 or not _in_downtrend(candles):
            return Signal.NEUTRAL
        c1, c2, c3 = candles.iloc[-3], candles.iloc[-2], candles.iloc[-1]
        if not (_is_bearish(c1) and _is_long_body(c1) and _is_doji(c2) and _is_bullish(c3)):
            return Signal.NEUTRAL
        if not (_gap_down(c1, c2) and _gap_up(c2, c3)):
            return Signal.NEUTRAL
        return Signal.BULLISH


@register_pattern
class BearishAbandonedBabyPattern:
    id = "cs_bearish_abandoned_baby"
    name = "Bearish Abandoned Baby"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 3 or not _in_uptrend(candles):
            return Signal.NEUTRAL
        c1, c2, c3 = candles.iloc[-3], candles.iloc[-2], candles.iloc[-1]
        if not (_is_bullish(c1) and _is_long_body(c1) and _is_doji(c2) and _is_bearish(c3)):
            return Signal.NEUTRAL
        if not (_gap_up(c1, c2) and _gap_down(c2, c3)):
            return Signal.NEUTRAL
        return Signal.BEARISH


@register_pattern
class ThreeInsideUpPattern:
    id = "cs_three_inside_up"
    name = "Three Inside Up"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 3 or not _in_downtrend(candles):
            return Signal.NEUTRAL
        c1, c2, c3 = candles.iloc[-3], candles.iloc[-2], candles.iloc[-1]
        if not (_is_bearish(c1) and _is_long_body(c1) and _is_bullish(c2)):
            return Signal.NEUTRAL
        if not _body_inside(c2, c1):
            return Signal.NEUTRAL
        if not (_is_bullish(c3) and _body_top(c3) > _body_top(c1)):
            return Signal.NEUTRAL
        return Signal.BULLISH


@register_pattern
class ThreeInsideDownPattern:
    id = "cs_three_inside_down"
    name = "Three Inside Down"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 3 or not _in_uptrend(candles):
            return Signal.NEUTRAL
        c1, c2, c3 = candles.iloc[-3], candles.iloc[-2], candles.iloc[-1]
        if not (_is_bullish(c1) and _is_long_body(c1) and _is_bearish(c2)):
            return Signal.NEUTRAL
        if not _body_inside(c2, c1):
            return Signal.NEUTRAL
        if not (_is_bearish(c3) and _body_bottom(c3) < _body_bottom(c1)):
            return Signal.NEUTRAL
        return Signal.BEARISH


@register_pattern
class ThreeOutsideUpPattern:
    id = "cs_three_outside_up"
    name = "Three Outside Up"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 3 or not _in_downtrend(candles):
            return Signal.NEUTRAL
        c1, c2, c3 = candles.iloc[-3], candles.iloc[-2], candles.iloc[-1]
        if not (_is_bearish(c1) and _is_bullish(c2) and _is_bullish(c3)):
            return Signal.NEUTRAL
        if not (_body_bottom(c2) <= _body_bottom(c1) and _body_top(c2) >= _body_top(c1)):
            return Signal.NEUTRAL
        if _body_top(c3) <= _body_top(c2):
            return Signal.NEUTRAL
        return Signal.BULLISH


@register_pattern
class ThreeOutsideDownPattern:
    id = "cs_three_outside_down"
    name = "Three Outside Down"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 3 or not _in_uptrend(candles):
            return Signal.NEUTRAL
        c1, c2, c3 = candles.iloc[-3], candles.iloc[-2], candles.iloc[-1]
        if not (_is_bullish(c1) and _is_bearish(c2) and _is_bearish(c3)):
            return Signal.NEUTRAL
        if not (_body_top(c2) >= _body_top(c1) and _body_bottom(c2) <= _body_bottom(c1)):
            return Signal.NEUTRAL
        if _body_bottom(c3) >= _body_bottom(c2):
            return Signal.NEUTRAL
        return Signal.BEARISH


@register_pattern
class BullishKickerPattern:
    id = "cs_bullish_kicker"
    name = "Bullish Kicker"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 2:
            return Signal.NEUTRAL
        c1, c2 = candles.iloc[-2], candles.iloc[-1]
        c1_open, _, _, c1_close = _ohlc(c1)
        c2_open, _, _, c2_close = _ohlc(c2)
        if not (_is_bearish(c1) and _is_bullish(c2) and _is_long_body(c2)):
            return Signal.NEUTRAL
        if c2_open <= c1_open or c2_close <= c1_close:
            return Signal.NEUTRAL
        return Signal.BULLISH


@register_pattern
class BearishKickerPattern:
    id = "cs_bearish_kicker"
    name = "Bearish Kicker"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 2:
            return Signal.NEUTRAL
        c1, c2 = candles.iloc[-2], candles.iloc[-1]
        c1_open, _, _, c1_close = _ohlc(c1)
        c2_open, _, _, c2_close = _ohlc(c2)
        if not (_is_bullish(c1) and _is_bearish(c2) and _is_long_body(c2)):
            return Signal.NEUTRAL
        if c2_open >= c1_open or c2_close >= c1_close:
            return Signal.NEUTRAL
        return Signal.BEARISH


@register_pattern
class TweezerBottomPattern:
    id = "cs_tweezer_bottom"
    name = "Tweezer Bottom"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 2 or not _in_downtrend(candles):
            return Signal.NEUTRAL
        c1, c2 = candles.iloc[-2], candles.iloc[-1]
        if not (_is_bearish(c1) and _is_bullish(c2)):
            return Signal.NEUTRAL
        if not _near(float(c1["low"]), float(c2["low"])):
            return Signal.NEUTRAL
        return Signal.BULLISH


@register_pattern
class TweezerTopPattern:
    id = "cs_tweezer_top"
    name = "Tweezer Top"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 2 or not _in_uptrend(candles):
            return Signal.NEUTRAL
        c1, c2 = candles.iloc[-2], candles.iloc[-1]
        if not (_is_bullish(c1) and _is_bearish(c2)):
            return Signal.NEUTRAL
        if not _near(float(c1["high"]), float(c2["high"])):
            return Signal.NEUTRAL
        return Signal.BEARISH


def _rising_three_methods(candles: pd.DataFrame) -> bool:
    if len(candles) < 5:
        return False
    c1 = candles.iloc[-5]
    middle = [candles.iloc[i] for i in range(-4, -1)]
    c5 = candles.iloc[-1]
    if not (_is_bullish(c1) and _is_long_body(c1)):
        return False
    c1_low, c1_high = float(c1["low"]), float(c1["high"])
    for c in middle:
        if not _is_bearish(c):
            return False
        if float(c["low"]) < c1_low or float(c["high"]) > c1_high:
            return False
    if not (_is_bullish(c5) and _is_long_body(c5)):
        return False
    return float(c5["close"]) > c1_high


@register_pattern
class RisingThreeMethodsPattern:
    id = "cs_rising_three_methods"
    name = "Rising Three Methods"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if not _in_uptrend(candles):
            return Signal.NEUTRAL
        if _rising_three_methods(candles):
            return Signal.BULLISH
        return Signal.NEUTRAL


def _falling_three_methods(candles: pd.DataFrame) -> bool:
    if len(candles) < 5:
        return False
    c1 = candles.iloc[-5]
    middle = [candles.iloc[i] for i in range(-4, -1)]
    c5 = candles.iloc[-1]
    if not (_is_bearish(c1) and _is_long_body(c1)):
        return False
    c1_low, c1_high = float(c1["low"]), float(c1["high"])
    for c in middle:
        if not _is_bullish(c):
            return False
        if float(c["low"]) < c1_low or float(c["high"]) > c1_high:
            return False
    if not (_is_bearish(c5) and _is_long_body(c5)):
        return False
    return float(c5["close"]) < c1_low


@register_pattern
class BearishMatHoldPattern:
    id = "cs_bearish_mat_hold"
    name = "Bearish Mat Hold"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if not _in_downtrend(candles):
            return Signal.NEUTRAL
        if _falling_three_methods(candles):
            return Signal.BEARISH
        return Signal.NEUTRAL


@register_pattern
class BullishMatHoldPattern:
    id = "cs_bullish_mat_hold"
    name = "Bullish Mat Hold"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if not _in_uptrend(candles):
            return Signal.NEUTRAL
        if _rising_three_methods(candles):
            return Signal.BULLISH
        return Signal.NEUTRAL


@register_pattern
class ConcealingBabySwallowPattern:
    id = "cs_concealing_baby_swallow"
    name = "Concealing Baby Swallow"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 4 or not _in_downtrend(candles):
            return Signal.NEUTRAL
        c1, c2, c3, c4 = (candles.iloc[i] for i in range(-4, 0))
        if not all(_is_bearish(c) and _is_long_body(c) for c in (c1, c2, c4)):
            return Signal.NEUTRAL
        if not _gap_down(c2, c3):
            return Signal.NEUTRAL
        if not (_body_bottom(c4) <= _body_bottom(c3) and _body_top(c4) >= _body_top(c3)):
            return Signal.NEUTRAL
        return Signal.BULLISH


@register_pattern
class BullishSeparatingLinesPattern:
    id = "cs_bullish_separating_lines"
    name = "Bullish Separating Lines"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 2 or not _in_uptrend(candles):
            return Signal.NEUTRAL
        c1, c2 = candles.iloc[-2], candles.iloc[-1]
        c1_open, _, _, _ = _ohlc(c1)
        c2_open, _, _, _ = _ohlc(c2)
        if not (_is_bearish(c1) and _is_bullish(c2)):
            return Signal.NEUTRAL
        if not _near(c1_open, c2_open):
            return Signal.NEUTRAL
        return Signal.BULLISH


@register_pattern
class BullishBeltHoldPattern:
    id = "cs_bullish_belt_hold"
    name = "Bullish Belt Hold"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 20 or not _in_downtrend(candles):
            return Signal.NEUTRAL
        last = candles.iloc[-1]
        if not (_is_bullish(last) and _is_long_body(last)):
            return Signal.NEUTRAL
        o, h, l, c = _ohlc(last)
        if not _near(l, o) or upper_wick(last) > candle_body(last) * 0.3:
            return Signal.NEUTRAL
        return Signal.BULLISH


@register_pattern
class BearishBeltHoldPattern:
    id = "cs_bearish_belt_hold"
    name = "Bearish Belt Hold"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 20 or not _in_uptrend(candles):
            return Signal.NEUTRAL
        last = candles.iloc[-1]
        if not (_is_bearish(last) and _is_long_body(last)):
            return Signal.NEUTRAL
        o, h, l, c = _ohlc(last)
        if not _near(h, o) or lower_wick(last) > candle_body(last) * 0.3:
            return Signal.NEUTRAL
        return Signal.BEARISH


@register_pattern
class BullishThreeLineStrikePattern:
    id = "cs_bullish_three_line_strike"
    name = "Bullish Three-Line Strike"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 4 or not _in_uptrend(candles):
            return Signal.NEUTRAL
        c1, c2, c3, c4 = (candles.iloc[i] for i in range(-4, 0))
        if not all(_is_bullish(c) and _is_long_body(c) for c in (c1, c2, c3)):
            return Signal.NEUTRAL
        if not (_is_bearish(c4) and _is_long_body(c4)):
            return Signal.NEUTRAL
        c4_open, _, _, c4_close = _ohlc(c4)
        if c4_open <= _body_top(c3) or c4_close >= _body_bottom(c1):
            return Signal.NEUTRAL
        return Signal.BULLISH


@register_pattern
class BearishThreeLineStrikePattern:
    id = "cs_bearish_three_line_strike"
    name = "Bearish Three-Line Strike"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 4 or not _in_downtrend(candles):
            return Signal.NEUTRAL
        c1, c2, c3, c4 = (candles.iloc[i] for i in range(-4, 0))
        if not all(_is_bearish(c) and _is_long_body(c) for c in (c1, c2, c3)):
            return Signal.NEUTRAL
        if not (_is_bullish(c4) and _is_long_body(c4)):
            return Signal.NEUTRAL
        c4_open, _, _, c4_close = _ohlc(c4)
        if c4_open >= _body_bottom(c3) or c4_close <= _body_top(c1):
            return Signal.NEUTRAL
        return Signal.BEARISH


@register_pattern
class LadderBottomPattern:
    id = "cs_ladder_bottom"
    name = "Ladder Bottom"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 5 or not _in_downtrend(candles):
            return Signal.NEUTRAL
        c1, c2, c3, c4, c5 = (candles.iloc[i] for i in range(-5, 0))
        if not all(_is_bearish(c) and _is_long_body(c) for c in (c1, c2, c3)):
            return Signal.NEUTRAL
        if not _is_small_body(c4):
            return Signal.NEUTRAL
        if not (_is_bullish(c5) and _is_long_body(c5)):
            return Signal.NEUTRAL
        return Signal.BULLISH


@register_pattern
class MeetingLinesPattern:
    id = "cs_meeting_lines"
    name = "Meeting Lines"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 2 or not _in_downtrend(candles):
            return Signal.NEUTRAL
        c1, c2 = candles.iloc[-2], candles.iloc[-1]
        _, _, _, c1_close = _ohlc(c1)
        c2_open, _, _, c2_close = _ohlc(c2)
        if not (_is_bearish(c1) and _is_long_body(c1) and _is_bullish(c2) and _is_long_body(c2)):
            return Signal.NEUTRAL
        if c2_open >= c1_close:
            return Signal.NEUTRAL
        if not _near(c1_close, c2_close):
            return Signal.NEUTRAL
        return Signal.BULLISH


@register_pattern
class BearishDojiStarPattern:
    id = "cs_bearish_doji_star"
    name = "Bearish Doji Star"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 2 or not _in_uptrend(candles):
            return Signal.NEUTRAL
        c1, c2 = candles.iloc[-2], candles.iloc[-1]
        if not (_is_bullish(c1) and _is_long_body(c1) and _is_doji(c2)):
            return Signal.NEUTRAL
        return Signal.BEARISH


@register_pattern
class UpsideGapTwoCrowsPattern:
    id = "cs_upside_gap_two_crows"
    name = "Upside Gap Two Crows"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 3 or not _in_uptrend(candles):
            return Signal.NEUTRAL
        c1, c2, c3 = candles.iloc[-3], candles.iloc[-2], candles.iloc[-1]
        if not (_is_bullish(c1) and _is_long_body(c1)):
            return Signal.NEUTRAL
        if not (_is_bearish(c2) and _is_bearish(c3)):
            return Signal.NEUTRAL
        if not _gap_up(c1, c2):
            return Signal.NEUTRAL
        _, _, _, c2_close = _ohlc(c2)
        _, _, _, c3_close = _ohlc(c3)
        if c3_close >= c2_close:
            return Signal.NEUTRAL
        return Signal.BEARISH

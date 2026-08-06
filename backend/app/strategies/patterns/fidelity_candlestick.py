"""Fidelity-style candlestick patterns (harami cross, marubozu, spinning top, etc.)."""

from __future__ import annotations

import pandas as pd

from app.strategies.base import Signal
from app.strategies.indicators import candle_body, candle_range, lower_wick, sma, upper_wick
from app.strategies.registry import register_pattern

DOJI_BODY_RATIO = 0.1
LONG_BODY_RATIO = 0.55


def _ohlc(row) -> tuple[float, float, float, float]:
    return float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])


def _body_top(row) -> float:
    o, _, _, c = _ohlc(row)
    return max(o, c)


def _body_bottom(row) -> float:
    o, _, _, c = _ohlc(row)
    return min(o, c)


def _body_inside(inner, outer) -> bool:
    return _body_top(inner) <= _body_top(outer) and _body_bottom(inner) >= _body_bottom(outer)


def _is_doji(row, max_body_ratio: float = DOJI_BODY_RATIO) -> bool:
    rng = candle_range(row)
    if rng <= 0:
        return False
    return candle_body(row) / rng <= max_body_ratio


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
class HaramiCrossPattern:
    id = "cs_harami_cross"
    name = "Harami Cross"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 2:
            return Signal.NEUTRAL
        c1, c2 = candles.iloc[-2], candles.iloc[-1]
        if not _is_doji(c2):
            return Signal.NEUTRAL
        if not _body_inside(c2, c1):
            return Signal.NEUTRAL
        rng1 = candle_range(c1)
        if rng1 <= 0 or candle_body(c1) / rng1 < LONG_BODY_RATIO:
            return Signal.NEUTRAL
        o1, _, _, c1c = _ohlc(c1)
        if c1c < o1 and _in_downtrend(candles):
            return Signal.BULLISH
        if c1c > o1 and _in_uptrend(candles):
            return Signal.BEARISH
        return Signal.NEUTRAL


@register_pattern
class MarubozuPattern:
    id = "cs_marubozu"
    name = "Marubozu"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 1:
            return Signal.NEUTRAL
        last = candles.iloc[-1]
        body = candle_body(last)
        rng = candle_range(last)
        if rng <= 0 or body <= 0:
            return Signal.NEUTRAL
        if body / rng < LONG_BODY_RATIO:
            return Signal.NEUTRAL
        if upper_wick(last) > body * 0.05 or lower_wick(last) > body * 0.05:
            return Signal.NEUTRAL
        o, _, _, c = _ohlc(last)
        if c > o:
            return Signal.BULLISH
        if c < o:
            return Signal.BEARISH
        return Signal.NEUTRAL


@register_pattern
class SpinningTopPattern:
    id = "cs_spinning_top"
    name = "Spinning Top"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 20:
            return Signal.NEUTRAL
        last = candles.iloc[-1]
        body = candle_body(last)
        rng = candle_range(last)
        if rng <= 0 or body <= 0:
            return Signal.NEUTRAL
        body_ratio = body / rng
        if body_ratio > 0.35 or body_ratio < 0.05:
            return Signal.NEUTRAL
        upper = upper_wick(last)
        lower = lower_wick(last)
        if upper < body * 0.5 or lower < body * 0.5:
            return Signal.NEUTRAL
        close = candles["close"].astype(float)
        sma20 = sma(close, 20)
        if pd.isna(sma20.iloc[-1]):
            return Signal.NEUTRAL
        if close.iloc[-1] < sma20.iloc[-1]:
            return Signal.BULLISH
        if close.iloc[-1] > sma20.iloc[-1]:
            return Signal.BEARISH
        return Signal.NEUTRAL


@register_pattern
class HighWavePattern:
    id = "cs_high_wave"
    name = "High-Wave Candle"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 20:
            return Signal.NEUTRAL
        last = candles.iloc[-1]
        body = candle_body(last)
        rng = candle_range(last)
        if rng <= 0:
            return Signal.NEUTRAL
        if body / rng > 0.35:
            return Signal.NEUTRAL
        if upper_wick(last) < rng * 0.25 or lower_wick(last) < rng * 0.25:
            return Signal.NEUTRAL
        close = candles["close"].astype(float)
        sma20 = sma(close, 20)
        if pd.isna(sma20.iloc[-1]):
            return Signal.NEUTRAL
        if close.iloc[-1] < sma20.iloc[-1]:
            return Signal.BULLISH
        if close.iloc[-1] > sma20.iloc[-1]:
            return Signal.BEARISH
        return Signal.NEUTRAL


@register_pattern
class LongLeggedDojiPattern:
    id = "cs_long_legged_doji"
    name = "Long-Legged Doji"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 20:
            return Signal.NEUTRAL
        last = candles.iloc[-1]
        if not _is_doji(last):
            return Signal.NEUTRAL
        rng = candle_range(last)
        body = candle_body(last)
        if upper_wick(last) < 2 * max(body, rng * 0.05):
            return Signal.NEUTRAL
        if lower_wick(last) < 2 * max(body, rng * 0.05):
            return Signal.NEUTRAL
        close = candles["close"].astype(float)
        sma20 = sma(close, 20)
        if pd.isna(sma20.iloc[-1]):
            return Signal.NEUTRAL
        if close.iloc[-1] < sma20.iloc[-1]:
            return Signal.BULLISH
        if close.iloc[-1] > sma20.iloc[-1]:
            return Signal.BEARISH
        return Signal.NEUTRAL

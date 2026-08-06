import pandas as pd

from app.strategies.base import Signal
from app.strategies.indicators import (
    candle_body,
    candle_range,
    lower_wick,
    sma,
    upper_wick,
)
from app.strategies.registry import register_pattern


@register_pattern
class DojiPattern:
    id = "p10_doji"
    name = "Doji Reversal"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 20:
            return Signal.NEUTRAL
        last = candles.iloc[-1]
        body = candle_body(last)
        rng = candle_range(last)
        if rng <= 0:
            return Signal.NEUTRAL
        # Doji: very small body relative to range
        if body / rng > 0.1:
            return Signal.NEUTRAL
        close = candles["close"].astype(float)
        sma20 = sma(close, 20)
        if pd.isna(sma20.iloc[-1]):
            return Signal.NEUTRAL
        # After downtrend → expect bullish reversal; after uptrend → bearish
        if close.iloc[-1] < sma20.iloc[-1]:
            return Signal.BULLISH
        if close.iloc[-1] > sma20.iloc[-1]:
            return Signal.BEARISH
        return Signal.NEUTRAL


@register_pattern
class HammerPattern:
    id = "p11_hammer"
    name = "Hammer Reversal"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 20:
            return Signal.NEUTRAL
        last = candles.iloc[-1]
        body = candle_body(last)
        rng = candle_range(last)
        if rng <= 0 or body <= 0:
            return Signal.NEUTRAL
        lower = lower_wick(last)
        upper = upper_wick(last)
        # Hammer: long lower shadow, small upper shadow, small body at top
        is_hammer = lower >= 2 * body and upper <= body * 0.5
        if not is_hammer:
            return Signal.NEUTRAL
        close = candles["close"].astype(float)
        sma20 = sma(close, 20)
        if pd.isna(sma20.iloc[-1]):
            return Signal.NEUTRAL
        # Valid after decline
        if close.iloc[-1] < sma20.iloc[-1]:
            return Signal.BULLISH
        # Inverted hammer after uptrend → bearish
        if close.iloc[-1] > sma20.iloc[-1] and float(last["open"]) > float(last["close"]):
            return Signal.BEARISH
        return Signal.NEUTRAL


@register_pattern
class EngulfingPattern:
    id = "p7_engulfing"
    name = "Engulfing Candle"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 2:
            return Signal.NEUTRAL
        prev, last = candles.iloc[-2], candles.iloc[-1]
        prev_open, prev_close = float(prev["open"]), float(prev["close"])
        last_open, last_close = float(last["open"]), float(last["close"])
        # Bullish engulfing
        if prev_close < prev_open and last_close > last_open:
            if last_open <= prev_close and last_close >= prev_open:
                return Signal.BULLISH
        # Bearish engulfing
        if prev_close > prev_open and last_close < last_open:
            if last_open >= prev_close and last_close <= prev_open:
                return Signal.BEARISH
        return Signal.NEUTRAL

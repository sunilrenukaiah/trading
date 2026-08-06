import pandas as pd

from app.strategies.base import Signal
from app.strategies.indicators import sma
from app.strategies.registry import register_pattern


@register_pattern
class SmaTrendPattern:
    id = "p8_sma_trend"
    name = "Price vs SMA20 Trend"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 21:
            return Signal.NEUTRAL
        close = candles["close"].astype(float)
        sma20 = sma(close, 20)
        if pd.isna(sma20.iloc[-1]) or pd.isna(sma20.iloc[-2]):
            return Signal.NEUTRAL
        slope_up = sma20.iloc[-1] > sma20.iloc[-2]
        slope_down = sma20.iloc[-1] < sma20.iloc[-2]
        if close.iloc[-1] > sma20.iloc[-1] and slope_up:
            return Signal.BULLISH
        if close.iloc[-1] < sma20.iloc[-1] and slope_down:
            return Signal.BEARISH
        return Signal.NEUTRAL


@register_pattern
class SwingStructurePattern:
    id = "p9_swing_structure"
    name = "Swing Structure (5-day)"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 5:
            return Signal.NEUTRAL
        recent = candles.tail(5)
        highs = recent["high"].astype(float)
        lows = recent["low"].astype(float)
        higher_highs = all(highs.iloc[i] > highs.iloc[i - 1] for i in range(1, len(highs)))
        higher_lows = all(lows.iloc[i] > lows.iloc[i - 1] for i in range(1, len(lows)))
        lower_highs = all(highs.iloc[i] < highs.iloc[i - 1] for i in range(1, len(highs)))
        lower_lows = all(lows.iloc[i] < lows.iloc[i - 1] for i in range(1, len(lows)))
        if higher_highs and higher_lows:
            return Signal.BULLISH
        if lower_highs and lower_lows:
            return Signal.BEARISH
        return Signal.NEUTRAL


@register_pattern
class VolumeBreakoutPattern:
    id = "p12_volume_breakout"
    name = "Volume Breakout (20-day)"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 20:
            return Signal.NEUTRAL
        close = candles["close"].astype(float)
        volume = candles["volume"].astype(float)
        avg_vol = volume.rolling(20, min_periods=20).mean()
        high_20 = close.rolling(20, min_periods=20).max()
        low_20 = close.rolling(20, min_periods=20).min()
        if pd.isna(avg_vol.iloc[-1]):
            return Signal.NEUTRAL
        last_close = close.iloc[-1]
        last_vol = volume.iloc[-1]
        if last_close >= high_20.iloc[-1] and last_vol > 1.5 * avg_vol.iloc[-1]:
            return Signal.BULLISH
        if last_close <= low_20.iloc[-1] and last_vol > 1.5 * avg_vol.iloc[-1]:
            return Signal.BEARISH
        return Signal.NEUTRAL

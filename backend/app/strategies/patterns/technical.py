import pandas as pd

from app.strategies.base import Signal
from app.strategies.indicators import macd, rsi, sma
from app.strategies.registry import register_pattern


@register_pattern
class SmaCrossPattern:
    id = "p1_sma_cross"
    name = "SMA Crossover (5/20)"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 21:
            return Signal.NEUTRAL
        close = candles["close"].astype(float)
        sma5 = sma(close, 5)
        sma20 = sma(close, 20)
        if pd.isna(sma5.iloc[-1]) or pd.isna(sma20.iloc[-1]):
            return Signal.NEUTRAL
        if sma5.iloc[-2] <= sma20.iloc[-2] and sma5.iloc[-1] > sma20.iloc[-1]:
            return Signal.BULLISH
        if sma5.iloc[-2] >= sma20.iloc[-2] and sma5.iloc[-1] < sma20.iloc[-1]:
            return Signal.BEARISH
        return Signal.NEUTRAL


@register_pattern
class RsiMomentumPattern:
    id = "p2_rsi_momentum"
    name = "RSI (14) Momentum"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 16:
            return Signal.NEUTRAL
        close = candles["close"].astype(float)
        rsi_vals = rsi(close, 14)
        if pd.isna(rsi_vals.iloc[-1]) or pd.isna(rsi_vals.iloc[-2]):
            return Signal.NEUTRAL
        if rsi_vals.iloc[-2] <= 50 < rsi_vals.iloc[-1]:
            return Signal.BULLISH
        if rsi_vals.iloc[-2] >= 50 > rsi_vals.iloc[-1]:
            return Signal.BEARISH
        return Signal.NEUTRAL


@register_pattern
class MacdCrossPattern:
    id = "p3_macd_cross"
    name = "MACD Signal Cross (12,26,9)"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 27:
            return Signal.NEUTRAL
        close = candles["close"].astype(float)
        macd_line, signal_line = macd(close)
        if pd.isna(macd_line.iloc[-1]) or pd.isna(signal_line.iloc[-1]):
            return Signal.NEUTRAL
        if macd_line.iloc[-2] <= signal_line.iloc[-2] and macd_line.iloc[-1] > signal_line.iloc[-1]:
            return Signal.BULLISH
        if macd_line.iloc[-2] >= signal_line.iloc[-2] and macd_line.iloc[-1] < signal_line.iloc[-1]:
            return Signal.BEARISH
        return Signal.NEUTRAL

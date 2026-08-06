import pandas as pd

from app.strategies.base import Signal
from app.strategies.indicators import bollinger_bands, sma
from app.strategies.registry import register_pattern


@register_pattern
class BollingerMeanReversionPattern:
    id = "p4_bb_mean_reversion"
    name = "Bollinger Mean Reversion (20,2)"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 20:
            return Signal.NEUTRAL
        close = candles["close"].astype(float)
        upper, _, lower = bollinger_bands(close, 20, 2.0)
        last_close = close.iloc[-1]
        if pd.isna(upper.iloc[-1]) or pd.isna(lower.iloc[-1]):
            return Signal.NEUTRAL
        if last_close <= lower.iloc[-1]:
            return Signal.BULLISH
        if last_close >= upper.iloc[-1]:
            return Signal.BEARISH
        return Signal.NEUTRAL


@register_pattern
class BollingerBreakoutPattern:
    id = "p5_bb_breakout"
    name = "Bollinger Breakout (20,2)"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 21:
            return Signal.NEUTRAL
        close = candles["close"].astype(float)
        upper, mid, lower = bollinger_bands(close, 20, 2.0)
        if pd.isna(upper.iloc[-1]) or pd.isna(upper.iloc[-2]):
            return Signal.NEUTRAL
        # Breakout: close crosses above upper band = bullish momentum
        if close.iloc[-2] <= upper.iloc[-2] and close.iloc[-1] > upper.iloc[-1]:
            return Signal.BULLISH
        if close.iloc[-2] >= lower.iloc[-2] and close.iloc[-1] < lower.iloc[-1]:
            return Signal.BEARISH
        # Walking the band: sustained above mid with expansion
        bandwidth = (upper - lower) / mid
        if (
            close.iloc[-1] > mid.iloc[-1]
            and bandwidth.iloc[-1] > bandwidth.iloc[-5:].mean()
            and close.iloc[-1] > close.iloc[-2]
        ):
            return Signal.BULLISH
        if (
            close.iloc[-1] < mid.iloc[-1]
            and bandwidth.iloc[-1] > bandwidth.iloc[-5:].mean()
            and close.iloc[-1] < close.iloc[-2]
        ):
            return Signal.BEARISH
        return Signal.NEUTRAL


@register_pattern
class BollingerSqueezePattern:
    id = "p6_bb_squeeze"
    name = "Bollinger Squeeze Breakout (20,2)"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 21:
            return Signal.NEUTRAL
        close = candles["close"].astype(float)
        upper, mid, lower = bollinger_bands(close, 20, 2.0)
        bandwidth = (upper - lower) / mid.replace(0, pd.NA)
        if bandwidth.iloc[-6:-1].isna().any() or pd.isna(bandwidth.iloc[-1]):
            return Signal.NEUTRAL
        # Squeeze: bandwidth was in lowest 20% of recent range, now expanding
        recent_bw = bandwidth.iloc[-20:]
        squeeze_threshold = recent_bw.quantile(0.2)
        was_squeeze = bandwidth.iloc[-2] <= squeeze_threshold
        expanding = bandwidth.iloc[-1] > bandwidth.iloc[-2]
        if was_squeeze and expanding:
            if close.iloc[-1] > mid.iloc[-1]:
                return Signal.BULLISH
            if close.iloc[-1] < mid.iloc[-1]:
                return Signal.BEARISH
        return Signal.NEUTRAL

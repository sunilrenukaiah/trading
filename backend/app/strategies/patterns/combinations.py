import pandas as pd

from app.strategies.base import Pattern, Signal
from app.strategies.registry import get_pattern, register_pattern


class CombinationPattern(Pattern):
    def __init__(self, pattern_id: str, name: str, component_ids: list[str]):
        self.id = pattern_id
        self.name = name
        self.component_ids = component_ids
        self.lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        raise NotImplementedError


@register_pattern
class SmaCrossRsiFilter:
    id = "c1_sma_cross_rsi"
    name = "SMA Cross + RSI Filter"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        sma_pat = get_pattern("p1_sma_cross")
        rsi_vals = _rsi_series(candles)
        signal = sma_pat.evaluate(candles)
        if signal == Signal.NEUTRAL or rsi_vals is None:
            return Signal.NEUTRAL
        if signal == Signal.BULLISH and rsi_vals.iloc[-1] < 70:
            return Signal.BULLISH
        if signal == Signal.BEARISH and rsi_vals.iloc[-1] > 30:
            return Signal.BEARISH
        return Signal.NEUTRAL


@register_pattern
class MacdSmaTrendCombo:
    id = "c2_macd_sma_trend"
    name = "MACD + SMA20 Trend"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        macd_sig = get_pattern("p3_macd_cross").evaluate(candles)
        trend_sig = get_pattern("p8_sma_trend").evaluate(candles)
        if macd_sig == Signal.NEUTRAL or trend_sig == Signal.NEUTRAL:
            return Signal.NEUTRAL
        if macd_sig == trend_sig:
            return macd_sig
        return Signal.NEUTRAL


@register_pattern
class EngulfingVolumeCombo:
    id = "c3_engulfing_volume"
    name = "Engulfing + Volume"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        engulf = get_pattern("p7_engulfing").evaluate(candles)
        if engulf == Signal.NEUTRAL:
            return Signal.NEUTRAL
        volume = candles["volume"].astype(float)
        avg_vol = volume.rolling(20, min_periods=20).mean()
        if pd.isna(avg_vol.iloc[-1]) or volume.iloc[-1] <= 1.2 * avg_vol.iloc[-1]:
            return Signal.NEUTRAL
        return engulf


@register_pattern
class HammerBollingerCombo:
    id = "c4_hammer_bb"
    name = "Hammer + BB Lower Band"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        hammer = get_pattern("p11_hammer").evaluate(candles)
        bb_rev = get_pattern("p4_bb_mean_reversion").evaluate(candles)
        if hammer == Signal.BULLISH and bb_rev == Signal.BULLISH:
            return Signal.BULLISH
        return Signal.NEUTRAL


@register_pattern
class DojiBollingerCombo:
    id = "c5_doji_bb"
    name = "Doji + BB Mean Reversion"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        doji = get_pattern("p10_doji").evaluate(candles)
        bb_rev = get_pattern("p4_bb_mean_reversion").evaluate(candles)
        if doji == Signal.NEUTRAL or bb_rev == Signal.NEUTRAL:
            return Signal.NEUTRAL
        if doji == bb_rev:
            return doji
        return Signal.NEUTRAL


def _rsi_series(candles: pd.DataFrame):
    from app.strategies.indicators import rsi

    close = candles["close"].astype(float)
    if len(close) < 15:
        return None
    return rsi(close, 14)

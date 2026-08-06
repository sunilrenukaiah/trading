"""Fidelity-style indicator patterns: ADX, ATR, OBV, CMF, Stochastic, StochRSI."""

from __future__ import annotations

import pandas as pd

from app.strategies.base import Signal
from app.strategies.indicators import adx, atr, cmf, obv, rsi, stoch_rsi, stochastic
from app.strategies.registry import register_pattern


@register_pattern
class AdxTrendPattern:
    id = "p13_adx_trend"
    name = "ADX Trend Strength"
    lookback_days = 30

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 30:
            return Signal.NEUTRAL
        high = candles["high"].astype(float)
        low = candles["low"].astype(float)
        close = candles["close"].astype(float)
        plus_di, minus_di, adx_line = adx(high, low, close, 14)
        if pd.isna(adx_line.iloc[-1]) or adx_line.iloc[-1] < 25:
            return Signal.NEUTRAL
        if plus_di.iloc[-1] > minus_di.iloc[-1] and plus_di.iloc[-1] > plus_di.iloc[-2]:
            return Signal.BULLISH
        if minus_di.iloc[-1] > plus_di.iloc[-1] and minus_di.iloc[-1] > minus_di.iloc[-2]:
            return Signal.BEARISH
        return Signal.NEUTRAL


@register_pattern
class AtrExpansionPattern:
    id = "p14_atr_expansion"
    name = "ATR Volatility Expansion"
    lookback_days = 25

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 25:
            return Signal.NEUTRAL
        high = candles["high"].astype(float)
        low = candles["low"].astype(float)
        close = candles["close"].astype(float)
        atr_line = atr(high, low, close, 14)
        if pd.isna(atr_line.iloc[-1]) or pd.isna(atr_line.iloc[-2]):
            return Signal.NEUTRAL
        avg_atr = atr_line.tail(20).mean()
        if atr_line.iloc[-1] <= avg_atr * 1.2:
            return Signal.NEUTRAL
        if close.iloc[-1] > close.iloc[-2]:
            return Signal.BULLISH
        if close.iloc[-1] < close.iloc[-2]:
            return Signal.BEARISH
        return Signal.NEUTRAL


@register_pattern
class ObvBreakoutPattern:
    id = "p15_obv_breakout"
    name = "OBV Breakout"
    lookback_days = 25

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 25:
            return Signal.NEUTRAL
        close = candles["close"].astype(float)
        volume = candles["volume"].astype(float)
        obv_line = obv(close, volume)
        obv_high = obv_line.rolling(20, min_periods=20).max()
        obv_low = obv_line.rolling(20, min_periods=20).min()
        if pd.isna(obv_high.iloc[-1]):
            return Signal.NEUTRAL
        if obv_line.iloc[-1] >= obv_high.iloc[-1] and close.iloc[-1] > close.iloc[-2]:
            return Signal.BULLISH
        if obv_line.iloc[-1] <= obv_low.iloc[-1] and close.iloc[-1] < close.iloc[-2]:
            return Signal.BEARISH
        return Signal.NEUTRAL


@register_pattern
class CmfFlowPattern:
    id = "p16_cmf_flow"
    name = "Chaikin Money Flow"
    lookback_days = 25

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 25:
            return Signal.NEUTRAL
        high = candles["high"].astype(float)
        low = candles["low"].astype(float)
        close = candles["close"].astype(float)
        volume = candles["volume"].astype(float)
        cmf_line = cmf(high, low, close, volume, 20)
        if pd.isna(cmf_line.iloc[-1]) or pd.isna(cmf_line.iloc[-2]):
            return Signal.NEUTRAL
        if cmf_line.iloc[-1] > 0.05 and cmf_line.iloc[-1] > cmf_line.iloc[-2]:
            return Signal.BULLISH
        if cmf_line.iloc[-1] < -0.05 and cmf_line.iloc[-1] < cmf_line.iloc[-2]:
            return Signal.BEARISH
        return Signal.NEUTRAL


@register_pattern
class StochasticPattern:
    id = "p17_stochastic"
    name = "Stochastic Oscillator"
    lookback_days = 20

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 20:
            return Signal.NEUTRAL
        high = candles["high"].astype(float)
        low = candles["low"].astype(float)
        close = candles["close"].astype(float)
        k, d = stochastic(high, low, close, 14, 3)
        if pd.isna(k.iloc[-1]) or pd.isna(d.iloc[-1]):
            return Signal.NEUTRAL
        if k.iloc[-2] <= d.iloc[-2] and k.iloc[-1] > d.iloc[-1] and k.iloc[-1] < 80:
            return Signal.BULLISH
        if k.iloc[-2] >= d.iloc[-2] and k.iloc[-1] < d.iloc[-1] and k.iloc[-1] > 20:
            return Signal.BEARISH
        return Signal.NEUTRAL


@register_pattern
class StochRsiPattern:
    id = "p18_stoch_rsi"
    name = "Stochastic RSI"
    lookback_days = 25

    def evaluate(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < 25:
            return Signal.NEUTRAL
        close = candles["close"].astype(float)
        rsi_line = rsi(close, 14)
        k, d = stoch_rsi(rsi_line, 14, 14, 3)
        if pd.isna(k.iloc[-1]) or pd.isna(d.iloc[-1]):
            return Signal.NEUTRAL
        if k.iloc[-2] <= d.iloc[-2] and k.iloc[-1] > d.iloc[-1] and k.iloc[-1] < 80:
            return Signal.BULLISH
        if k.iloc[-2] >= d.iloc[-2] and k.iloc[-1] < d.iloc[-1] and k.iloc[-1] > 20:
            return Signal.BEARISH
        return Signal.NEUTRAL

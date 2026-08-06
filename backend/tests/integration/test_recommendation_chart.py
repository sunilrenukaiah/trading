"""Recommendation chart builder tests."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services.recommendation_engine import StockRecommendation
from ui.recommendation_chart import build_recommendation_chart, pattern_candle_span


class _Candle:
    def __init__(self, trade_date, open_, high, low, close):
        self.trade_date = trade_date
        self.open = open_
        self.high = high
        self.low = low
        self.close = close


def _sample_rec(pattern_id: str = "cs_bullish_harami") -> StockRecommendation:
    return StockRecommendation(
        symbol="INFY",
        cap_tier="Large Cap",
        pattern_id=pattern_id,
        pattern_name="Bullish Harami",
        pattern_hit_rate_30d=62.0,
        signal="BULLISH",
        action="BUY",
        buy_price=100.0,
        stop_loss=95.0,
        resistance=110.0,
        sell_price=115.0,
        actual_sell_price=107.5,
        model_profit_pct=15.0,
        actual_profit_pct=7.5,
        risk_reward=1.5,
        latest_close=100.0,
        prev_close=99.0,
        expected_move_pct=7.5,
        confidence_score=72.0,
    )


def _sample_candles(n: int = 35):
    start = date(2026, 1, 1)
    candles = []
    price = 110.0
    for i in range(n):
        drift = -0.4 if i < n - 5 else 0.2
        o = price
        c = price + drift
        h = max(o, c) + 0.5
        l = min(o, c) - 0.5
        candles.append(_Candle(start + timedelta(days=i), o, h, l, c))
        price = c
    return candles


@pytest.mark.quick
def test_pattern_candle_span_defaults() -> None:
    assert pattern_candle_span("cs_three_white_soldiers") == 3
    assert pattern_candle_span("unknown_pattern") == 3


@pytest.mark.quick
def test_build_recommendation_chart_returns_figure() -> None:
    fig = build_recommendation_chart(_sample_rec(), _sample_candles(), lookback_days=20)
    assert fig.data
    assert any(getattr(trace, "type", "") == "candlestick" for trace in fig.data)

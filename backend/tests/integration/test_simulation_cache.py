"""Simulation cache serialize/deserialize roundtrip."""

from __future__ import annotations

from datetime import date

import pytest

from app.services.backtest import BacktestReport, DayResult, PatternResult
from app.services.simulation_cache import deserialize_report, serialize_report
from app.strategies.base import Signal


def _sample_report() -> BacktestReport:
    day = DayResult(
        trade_date=date(2026, 7, 28),
        symbol="RELIANCE",
        signal=Signal.BULLISH,
        actual=Signal.BULLISH,
        correct=True,
        prev_close=100.0,
        predicted_close=102.0,
        actual_close=101.5,
    )
    pattern = PatternResult(
        pattern_id="doji",
        pattern_name="Doji",
        total_correct=1,
        total_signals=1,
        daily_scores=[1],
        stock_correct={"RELIANCE": 1},
        stock_signals={"RELIANCE": 1},
        day_details=[day],
    )
    return BacktestReport(
        eval_days=30,
        lookback_days=20,
        stock_count=1,
        patterns=[pattern],
        universe="NIFTY50",
        symbols=["RELIANCE"],
    )


@pytest.mark.quick
def test_serialize_deserialize_roundtrip() -> None:
    original = _sample_report()
    payload = serialize_report(original)
    restored = deserialize_report(payload)

    assert restored.eval_days == original.eval_days
    assert restored.universe == "NIFTY50"
    assert restored.symbols == ["RELIANCE"]
    assert len(restored.patterns) == 1
    assert restored.patterns[0].pattern_id == "doji"
    assert restored.patterns[0].day_details[0].signal == Signal.BULLISH

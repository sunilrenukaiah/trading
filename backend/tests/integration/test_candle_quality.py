"""OHLCV price validation tests."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from app.services.candle_quality import min_price_for_tier, validate_series_local


@pytest.mark.quick
def test_min_price_for_tier() -> None:
    assert min_price_for_tier("large_cap") == 100.0
    assert min_price_for_tier("mid_cap") == 30.0


@pytest.mark.quick
def test_validate_series_local_rejects_corrupt_large_cap_price() -> None:
    bad = pd.DataFrame(
        {
            "trade_date": [date(2026, 7, d) for d in range(1, 27)],
            "open": [12.0] * 26,
            "high": [13.0] * 26,
            "low": [11.0] * 26,
            "close": [12.57] * 26,
            "volume": [1000] * 26,
        }
    )
    assert validate_series_local("INFY", bad, tier="large_cap") is None


@pytest.mark.quick
def test_validate_series_local_accepts_valid_series() -> None:
    good = pd.DataFrame(
        {
            "trade_date": [date(2026, 7, d) for d in range(1, 27)],
            "open": [1150.0] * 26,
            "high": [1160.0] * 26,
            "low": [1145.0] * 26,
            "close": [1153.0] * 26,
            "volume": [1000] * 26,
        }
    )
    result = validate_series_local("INFY", good, tier="large_cap")
    assert result is not None
    assert float(result.iloc[-1]["close"]) == 1153.0

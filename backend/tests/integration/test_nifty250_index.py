"""Tests for NIFTY250 composite index."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from app.services.nifty250_index import (
    build_nifty250_composite_candles,
    composite_change_pct,
)


def _frame(closes: list[float], start: date = date(2026, 7, 1)) -> pd.DataFrame:
    rows = []
    for i, close in enumerate(closes):
        d = start.toordinal() + i
        trade = date.fromordinal(d)
        rows.append(
            {
                "trade_date": trade,
                "open": close - 1,
                "high": close + 1,
                "low": close - 2,
                "close": close,
                "volume": 100,
            }
        )
    return pd.DataFrame(rows)


@pytest.mark.quick
def test_build_nifty250_composite_candles_averages_constituents() -> None:
    symbol_data = {
        "AAA": _frame([100.0, 102.0, 104.0]),
        "BBB": _frame([200.0, 198.0, 202.0]),
    }
    candles = build_nifty250_composite_candles(symbol_data, days=2, min_symbols_per_day=2)
    assert len(candles) == 2
    assert float(candles[-1].close) == pytest.approx(153.0)


@pytest.mark.quick
def test_composite_change_pct() -> None:
    class _C:
        def __init__(self, close: Decimal) -> None:
            self.close = close

    candles = [_C(Decimal("100")), _C(Decimal("105"))]
    assert composite_change_pct(candles) == 5.0

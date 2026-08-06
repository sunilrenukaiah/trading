"""OHLCV price sanitization helpers (no provider imports)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

import pandas as pd


def finite_decimal(value) -> Decimal | None:
    """Return a finite Decimal or None for NaN/inf/missing values."""
    if value is None:
        return None
    try:
        d = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return d if d.is_finite() else None


def finite_float(value) -> float | None:
    d = finite_decimal(value)
    return float(d) if d is not None else None


def valid_candle_prices(open_, high, low, close) -> bool:
    prices = [finite_decimal(v) for v in (open_, high, low, close)]
    return all(p is not None and p > 0 for p in prices)


def sanitize_ohlcv_dataframe(df: pd.DataFrame | None) -> pd.DataFrame | None:
    """Drop rows with NaN/zero/invalid OHLCV."""
    if df is None or df.empty:
        return None
    rows = []
    for _, row in df.iterrows():
        open_ = finite_float(row.get("open"))
        high = finite_float(row.get("high"))
        low = finite_float(row.get("low"))
        close = finite_float(row.get("close"))
        if open_ is None or high is None or low is None or close is None:
            continue
        if open_ <= 0 or high <= 0 or low <= 0 or close <= 0:
            continue
        vol = finite_float(row.get("volume"))
        rows.append(
            {
                "trade_date": row["trade_date"],
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": int(vol) if vol is not None and vol >= 0 else 0,
            }
        )
    if not rows:
        return None
    return pd.DataFrame(rows).sort_values("trade_date").reset_index(drop=True)

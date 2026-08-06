"""Detect corrupt OHLCV series in local market data (no network access)."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from app.services.ohlcv_utils import finite_float, sanitize_ohlcv_dataframe

MAX_PRICE_DEVIATION = 0.35

MIN_PRICE_BY_TIER = {
    "large_cap": 100.0,
    "mid_cap": 30.0,
    "small_cap": 10.0,
}


def min_price_for_tier(tier: str) -> float:
    return MIN_PRICE_BY_TIER.get(tier, 10.0)


def candles_to_dataframe(candles) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "trade_date": c.trade_date,
                "open": float(c.open),
                "high": float(c.high),
                "low": float(c.low),
                "close": float(c.close),
                "volume": int(c.volume or 0),
            }
            for c in candles
        ]
    ).sort_values("trade_date")


def validate_series_local(
    symbol: str,
    df: pd.DataFrame,
    *,
    tier: str,
) -> pd.DataFrame | None:
    """
    Return a sanitized dataframe when local OHLCV looks usable.

    Does not call NSE/yfinance — run Refresh market data to fix gaps upstream.
    """
    del symbol  # kept for logging compatibility
    cleaned = sanitize_ohlcv_dataframe(df)
    if cleaned is None or len(cleaned) < 25:
        return None

    latest = finite_float(cleaned.iloc[-1]["close"])
    if latest is None or latest < min_price_for_tier(tier):
        return None

    return cleaned


# Backward-compatible alias — no longer refreshes from the network.
async def validate_or_refresh_series(
    symbol: str,
    df: pd.DataFrame,
    *,
    tier: str,
) -> pd.DataFrame | None:
    return validate_series_local(symbol, df, tier=tier)


async def fetch_nse_history(symbol: str, *, days: int = 120) -> pd.DataFrame | None:
    del symbol, days
    return None


async def nse_latest_close(symbol: str) -> float | None:
    del symbol
    return None

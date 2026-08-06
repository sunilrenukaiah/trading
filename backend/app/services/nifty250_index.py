"""Equal-weight NIFTY250 composite index from stored constituent OHLCV."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas import CandleOut
from app.services.recommendation_engine import load_market_universe_candles_from_db


def build_nifty250_composite_candles(
    symbol_data: dict[str, pd.DataFrame],
    *,
    days: int = 30,
    min_symbols_per_day: int | None = None,
) -> list[CandleOut]:
    """Cross-sectional mean OHLC across NIFTY250 names (local DB), last ``days`` sessions."""
    if not symbol_data or days < 1:
        return []

    min_names = min_symbols_per_day
    if min_names is None:
        min_names = max(10, len(symbol_data) // 5)

    field_frames: dict[str, pd.DataFrame] = {}
    for field in ("open", "high", "low", "close"):
        columns: dict[str, pd.Series] = {}
        for symbol, frame in symbol_data.items():
            if field not in frame.columns:
                continue
            indexed = frame.set_index("trade_date")[field]
            columns[symbol] = indexed
        if not columns:
            return []
        field_frames[field] = pd.DataFrame(columns).sort_index()

    close_frame = field_frames["close"]
    eligible = close_frame.notna().sum(axis=1) >= min_names
    if not eligible.any():
        return []

    averaged: dict[str, pd.Series] = {}
    for field, frame in field_frames.items():
        averaged[field] = frame.loc[eligible].mean(axis=1, skipna=True)

    window = averaged["close"].tail(days)
    if window.empty:
        return []

    out: list[CandleOut] = []
    for trade_date in window.index:
        ts = trade_date.date() if hasattr(trade_date, "date") else trade_date
        if not isinstance(ts, date):
            ts = pd.Timestamp(trade_date).date()
        open_ = float(averaged["open"].loc[trade_date])
        high = float(averaged["high"].loc[trade_date])
        low = float(averaged["low"].loc[trade_date])
        close = float(averaged["close"].loc[trade_date])
        out.append(
            CandleOut(
                trade_date=ts,
                open=Decimal(str(round(open_, 4))),
                high=Decimal(str(round(high, 4))),
                low=Decimal(str(round(low, 4))),
                close=Decimal(str(round(close, 4))),
                volume=0,
            )
        )
    return out


def composite_change_pct(candles: list[CandleOut]) -> float | None:
    if len(candles) < 2:
        return None
    prev = float(candles[-2].close)
    last = float(candles[-1].close)
    if prev == 0:
        return None
    return round((last - prev) / prev * 100, 2)


async def load_nifty250_composite_candles(
    session: AsyncSession,
    *,
    days: int = 30,
) -> list[CandleOut]:
    """Load equal-weight NIFTY250 composite candles for charting."""
    symbol_data = await load_market_universe_candles_from_db(session, min_rows=days + 5)
    return build_nifty250_composite_candles(symbol_data, days=days)

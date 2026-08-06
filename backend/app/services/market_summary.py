"""Build market summary rows with sanitized OHLCV prices."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Instrument, OhlcvCandle
from app.schemas import MarketSummaryItem
from app.services.ohlcv_utils import finite_decimal


async def fetch_market_summary(session: AsyncSession) -> list[MarketSummaryItem]:
    instruments = (
        await session.scalars(select(Instrument).where(Instrument.is_active.is_(True)))
    ).all()

    summary: list[MarketSummaryItem] = []
    for inst in instruments:
        candles = (
            await session.scalars(
                select(OhlcvCandle)
                .where(OhlcvCandle.instrument_id == inst.id)
                .order_by(OhlcvCandle.trade_date.desc())
                .limit(5)
            )
        ).all()

        finite_closes = []
        for candle in candles:
            close = finite_decimal(candle.close)
            if close is not None:
                finite_closes.append(close)
            if len(finite_closes) >= 2:
                break

        last_close = finite_closes[0] if len(finite_closes) >= 1 else None
        prev_close = finite_closes[1] if len(finite_closes) >= 2 else None
        change_pct = None
        if last_close is not None and prev_close is not None and prev_close != 0:
            change_pct = float((last_close - prev_close) / prev_close * 100)

        summary.append(
            MarketSummaryItem(
                symbol=inst.symbol,
                name=inst.name,
                instrument_type=inst.instrument_type,
                last_close=last_close,
                prev_close=prev_close,
                change_pct=change_pct,
            )
        )

    summary.sort(key=lambda x: (x.instrument_type.value != "INDEX", x.symbol))
    return summary

"""Upsert partial session OHLC for today's trading day (mid-day recommendation analysis)."""

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Instrument, OhlcvCandle
from app.providers.base import CandleData
from app.services.ingestion import market_data_sync_symbols, upsert_candles
from app.services.intraday_chart import fetch_session_ohlc_sync
from app.services.app_logger import get_logger
from app.services.market_calendar import current_session_date, is_midday_analysis_ready
from app.services.ohlcv_utils import valid_candle_prices

log = get_logger(__name__)

# Skip API fetch when today's session candle was synced recently.
SESSION_OHLC_FRESH_MINUTES = 10
SESSION_OHLC_MAX_CONCURRENT = 5
SESSION_OHLC_EST_SEC_PER_SYMBOL = 1.5
SESSION_OHLC_POST_FETCH_DELAY_SEC = 0.1


def format_eta(seconds: float | None) -> str:
    """Human-readable remaining time for progress UI."""
    if seconds is None or seconds <= 0:
        return ""
    seconds = max(1, int(round(seconds)))
    if seconds < 60:
        return f"~{seconds}s left"
    minutes, secs = divmod(seconds, 60)
    if secs == 0:
        return f"~{minutes}m left"
    return f"~{minutes}m {secs}s left"


def session_ohlc_progress_message(
    *,
    symbol: str | None,
    completed: int,
    total: int,
    eta_sec: float | None,
    fresh_skipped: int,
) -> str:
    if symbol:
        head = f"Session OHLC · {symbol} ({completed}/{total})"
    else:
        head = f"Session OHLC ({completed}/{total})"
    parts = [head]
    eta = format_eta(eta_sec)
    if eta:
        parts.append(eta)
    if fresh_skipped:
        parts.append(f"{fresh_skipped} fresh skipped")
    return " · ".join(parts)


async def _fresh_instrument_ids(
    session: AsyncSession,
    *,
    trade_date: date,
    instrument_ids: list[int],
    freshness_minutes: int,
) -> set[int]:
    if not instrument_ids:
        return set()

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=freshness_minutes)
    candles = (
        await session.scalars(
            select(OhlcvCandle).where(
                OhlcvCandle.trade_date == trade_date,
                OhlcvCandle.instrument_id.in_(instrument_ids),
                OhlcvCandle.synced_at >= cutoff,
            )
        )
    ).all()
    fresh: set[int] = set()
    for candle in candles:
        if valid_candle_prices(candle.open, candle.high, candle.low, candle.close):
            fresh.add(candle.instrument_id)
    return fresh


async def _fetch_session_quotes_parallel(
    symbols: list[str],
    *,
    max_concurrent: int,
    post_fetch_delay_sec: float,
    progress_callback,
    fresh_skipped: int,
) -> dict[str, dict]:
    if not symbols:
        return {}

    sem = asyncio.Semaphore(max(1, max_concurrent))
    completed = 0
    lock = asyncio.Lock()
    total = len(symbols)
    results: dict[str, dict] = {}
    started_at = time.perf_counter()

    if progress_callback:
        est_sec = total * SESSION_OHLC_EST_SEC_PER_SYMBOL / max(1, max_concurrent)
        progress_callback(
            0,
            total,
            session_ohlc_progress_message(
                symbol=None,
                completed=0,
                total=total,
                eta_sec=est_sec,
                fresh_skipped=fresh_skipped,
            ),
        )

    async def fetch_one(symbol: str) -> None:
        nonlocal completed
        async with sem:
            quote = await asyncio.to_thread(fetch_session_ohlc_sync, symbol)
            if post_fetch_delay_sec > 0:
                await asyncio.sleep(post_fetch_delay_sec)
        async with lock:
            results[symbol] = quote
            completed += 1
            if progress_callback:
                elapsed = time.perf_counter() - started_at
                avg = elapsed / completed
                remaining = max(0, total - completed)
                eta_sec = (remaining / max(1, max_concurrent)) * avg
                progress_callback(
                    completed,
                    total,
                    session_ohlc_progress_message(
                        symbol=symbol,
                        completed=completed,
                        total=total,
                        eta_sec=eta_sec,
                        fresh_skipped=fresh_skipped,
                    ),
                )

    await asyncio.gather(*(fetch_one(symbol) for symbol in symbols))
    return results


async def upsert_intraday_session_candles(
    session: AsyncSession,
    *,
    progress_callback=None,
    freshness_minutes: int = SESSION_OHLC_FRESH_MINUTES,
    max_concurrent: int = SESSION_OHLC_MAX_CONCURRENT,
) -> dict[str, int | date | str]:
    """
    Pull today's session OHLC for the sync universe and upsert into ohlcv_candles.

    Skips symbols whose today's candle was synced within ``freshness_minutes``.
    Fetches remaining symbols in parallel with a concurrency limit.
    """
    if not is_midday_analysis_ready():
        raise ValueError(
            "Mid-day market sync is only available from 11:45 AM to 4:30 PM IST on trading days."
        )

    trade_date = current_session_date()
    symbols = await market_data_sync_symbols(session)
    total = len(symbols)
    log.info(
        "Mid-day session OHLC sync starting trade_date=%s symbols=%s",
        trade_date,
        total,
    )
    upserted = 0
    skipped = 0
    failed = 0
    fresh_skipped = 0

    instruments = (
        await session.scalars(select(Instrument).where(Instrument.symbol.in_(symbols)))
    ).all()
    by_symbol = {inst.symbol.upper(): inst for inst in instruments}
    instrument_ids = [inst.id for inst in instruments]
    fresh_ids = await _fresh_instrument_ids(
        session,
        trade_date=trade_date,
        instrument_ids=instrument_ids,
        freshness_minutes=freshness_minutes,
    )

    symbols_to_fetch: list[str] = []
    for symbol in symbols:
        instrument = by_symbol.get(symbol.upper())
        if instrument is None:
            skipped += 1
            continue
        if instrument.id in fresh_ids:
            fresh_skipped += 1
            continue
        symbols_to_fetch.append(symbol)

    log.info(
        "Mid-day session OHLC plan trade_date=%s fetch=%s fresh_skipped=%s missing_instrument=%s",
        trade_date,
        len(symbols_to_fetch),
        fresh_skipped,
        skipped,
    )

    quotes_by_symbol = await _fetch_session_quotes_parallel(
        symbols_to_fetch,
        max_concurrent=max_concurrent,
        post_fetch_delay_sec=SESSION_OHLC_POST_FETCH_DELAY_SEC,
        progress_callback=progress_callback,
        fresh_skipped=fresh_skipped,
    )

    for symbol in symbols_to_fetch:
        instrument = by_symbol.get(symbol.upper())
        if instrument is None:
            skipped += 1
            continue

        quote = quotes_by_symbol.get(symbol, {})
        open_ = quote.get("open")
        high = quote.get("high")
        low = quote.get("low")
        close = quote.get("last") or quote.get("close")

        if open_ is None or high is None or low is None or close is None:
            failed += 1
            continue

        open_d = Decimal(str(round(float(open_), 4)))
        high_d = Decimal(str(round(float(high), 4)))
        low_d = Decimal(str(round(float(low), 4)))
        close_d = Decimal(str(round(float(close), 4)))

        if not valid_candle_prices(open_d, high_d, low_d, close_d):
            failed += 1
            continue

        candle = CandleData(
            trade_date=trade_date,
            open=open_d,
            high=high_d,
            low=low_d,
            close=close_d,
            volume=0,
        )
        upserted += await upsert_candles(session, instrument.id, [candle])

    await session.commit()
    stats = {
        "trade_date": trade_date,
        "symbols_total": total,
        "candles_upserted": upserted,
        "symbols_skipped": skipped,
        "symbols_fresh_skipped": fresh_skipped,
        "symbols_failed": failed,
        "symbols_fetched": len(symbols_to_fetch),
    }
    log.info("Mid-day session OHLC sync finished %s", stats)
    return stats

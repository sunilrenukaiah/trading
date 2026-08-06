"""Live LTP quotes for open positions (not bulk OHLCV sync)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Instrument, OhlcvCandle
from app.providers import get_market_data_provider
from app.providers.base import SessionQuote
from app.services.ingestion import provider_market_symbol
from app.services.market_calendar import active_market_session_date, get_previous_trading_day
from app.services.ohlcv_utils import finite_decimal

_poll_extremes: dict[str, tuple[Decimal, Decimal]] = {}
_poll_extremes_date: date | None = None


@dataclass(frozen=True)
class PositionLiveQuote:
    """Cached live quote fields for the Positions table."""

    last_price: float
    today_open: float | None = None
    prev_close: float | None = None
    session_high: float | None = None

    @classmethod
    def from_session_quote(cls, quote: SessionQuote) -> PositionLiveQuote:
        high = quote.observed_high
        return cls(
            last_price=float(quote.last_price),
            today_open=_decimal_to_float(quote.day_open),
            prev_close=_decimal_to_float(quote.prev_close),
            session_high=float(high) if high is not None else None,
        )

    def to_cache(self) -> dict[str, float | None]:
        return {
            "ltp": self.last_price,
            "open": self.today_open,
            "prev_close": self.prev_close,
            "high": self.session_high,
        }

    @classmethod
    def from_cache(cls, raw: Any) -> PositionLiveQuote | None:
        if raw is None:
            return None
        if isinstance(raw, PositionLiveQuote):
            return raw
        if isinstance(raw, (int, float)):
            return cls(last_price=float(raw))
        if isinstance(raw, dict):
            ltp = raw.get("ltp")
            if ltp is None:
                return None
            return cls(
                last_price=float(ltp),
                today_open=_maybe_float(raw.get("open")),
                prev_close=_maybe_float(raw.get("prev_close")),
                session_high=_maybe_float(raw.get("high")),
            )
        return None


def _maybe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _decimal_to_float(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value)


def _quote_field(quote: Any, name: str) -> Decimal | None:
    """Read a QuoteData field; tolerate stale provider instances missing new attrs."""
    value = getattr(quote, name, None)
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (TypeError, ValueError, ArithmeticError):
        return None


def live_quote_ltp(quotes: dict[str, Any], symbol: str) -> float | None:
    parsed = PositionLiveQuote.from_cache(quotes.get(symbol))
    return parsed.last_price if parsed else None


def _reset_poll_extremes_if_new_day() -> None:
    global _poll_extremes_date
    today = date.today()
    if _poll_extremes_date != today:
        _poll_extremes.clear()
        _poll_extremes_date = today


def merge_poll_extremes(quotes: dict[str, SessionQuote]) -> dict[str, SessionQuote]:
    """Accumulate min/max LTP seen across live poll cycles (e.g. every 10s)."""
    _reset_poll_extremes_if_new_day()
    merged: dict[str, SessionQuote] = {}
    for sym, quote in quotes.items():
        ltp = quote.last_price
        seed_high = quote.nse_day_high if quote.nse_day_high is not None else ltp
        seed_low = ltp
        prev = _poll_extremes.get(sym)
        if prev is not None:
            high = max(ltp, prev[0], seed_high)
            low = min(ltp, prev[1])
        else:
            high = max(ltp, seed_high)
            low = min(ltp, seed_low)
        _poll_extremes[sym] = (high, low)
        merged[sym] = SessionQuote(
            last_price=ltp,
            poll_high=high,
            poll_low=low,
            day_open=quote.day_open,
            prev_close=quote.prev_close,
            nse_day_high=quote.nse_day_high,
        )
    return merged


# Backward-compatible alias for streamlit_imports / tests.
merge_session_extremes = merge_poll_extremes


def reset_poll_extremes() -> None:
    """Clear accumulated poll highs/lows (tests or when live polling stops)."""
    global _poll_extremes_date
    _poll_extremes.clear()
    _poll_extremes_date = None


async def _enrich_quotes_from_db(
    session: AsyncSession,
    instruments: list[Instrument],
    quotes: dict[str, SessionQuote],
) -> dict[str, SessionQuote]:
    """Fill missing day open / prev close from synced ohlcv_candles."""
    if not instruments:
        return quotes

    today = active_market_session_date()
    prior = get_previous_trading_day(today)
    ids = [inst.id for inst in instruments]
    inst_by_symbol = {inst.symbol: inst for inst in instruments}

    today_rows = (
        await session.scalars(
            select(OhlcvCandle).where(
                OhlcvCandle.instrument_id.in_(ids),
                OhlcvCandle.trade_date == today,
            )
        )
    ).all()
    prior_rows = (
        await session.scalars(
            select(OhlcvCandle).where(
                OhlcvCandle.instrument_id.in_(ids),
                OhlcvCandle.trade_date == prior,
            )
        )
    ).all()

    today_by_id = {row.instrument_id: row for row in today_rows}
    prior_by_id = {row.instrument_id: row for row in prior_rows}

    enriched: dict[str, SessionQuote] = {}
    for symbol, quote in quotes.items():
        inst = inst_by_symbol.get(symbol)
        if inst is None:
            enriched[symbol] = quote
            continue

        day_open = quote.day_open
        prev_close = quote.prev_close
        nse_day_high = quote.nse_day_high
        nse_day_low = quote.nse_day_low

        today_candle = today_by_id.get(inst.id)
        prior_candle = prior_by_id.get(inst.id)

        if day_open is None and today_candle is not None:
            day_open = finite_decimal(today_candle.open)
        if prev_close is None and prior_candle is not None:
            prev_close = finite_decimal(prior_candle.close)
        if nse_day_high is None and today_candle is not None:
            nse_day_high = finite_decimal(today_candle.high)
        if nse_day_low is None and today_candle is not None:
            nse_day_low = finite_decimal(today_candle.low)

        enriched[symbol] = SessionQuote(
            last_price=quote.last_price,
            poll_high=quote.poll_high,
            poll_low=quote.poll_low,
            day_open=day_open,
            prev_close=prev_close,
            nse_day_high=nse_day_high,
            nse_day_low=nse_day_low,
        )
    return enriched


async def fetch_live_quotes(
    session: AsyncSession,
    symbols: list[str],
) -> dict[str, SessionQuote]:
    """Fetch live LTP from NSE; bracket high/low come from poll accumulation, not NSE day OHLC alone."""
    normalized = sorted({s.upper() for s in symbols if s})
    if not normalized:
        return {}

    instruments = (
        await session.scalars(select(Instrument).where(Instrument.symbol.in_(normalized)))
    ).all()
    if not instruments:
        return {}

    provider = get_market_data_provider()
    market_symbols = [provider_market_symbol(inst) for inst in instruments]
    quotes = await provider.fetch_latest_quotes(market_symbols)

    result: dict[str, SessionQuote] = {}
    for inst in instruments:
        market_symbol = provider_market_symbol(inst)
        quote = (
            quotes.get(market_symbol)
            or quotes.get(inst.yfinance_symbol)
            or quotes.get(inst.symbol)
        )
        if quote is not None and quote.last_price is not None:
            result[inst.symbol] = SessionQuote(
                last_price=quote.last_price,
                day_open=_quote_field(quote, "day_open"),
                prev_close=_quote_field(quote, "prev_close"),
                nse_day_high=_quote_field(quote, "day_high"),
                nse_day_low=_quote_field(quote, "day_low"),
            )

    result = await _enrich_quotes_from_db(session, instruments, result)
    return merge_poll_extremes(result)


async def fetch_position_live_quotes(
    session: AsyncSession,
    symbols: list[str],
) -> dict[str, PositionLiveQuote]:
    """Live quotes shaped for the Positions table."""
    quotes = await fetch_live_quotes(session, symbols)
    return {sym: PositionLiveQuote.from_session_quote(q) for sym, q in quotes.items()}

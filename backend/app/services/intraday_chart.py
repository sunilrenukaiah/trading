"""Intraday price context for open position charts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import nsefeed as nf
import yfinance as yf
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Instrument, OhlcvCandle, PaperTradePlan, TradePlanStatus
from app.providers.yfinance_provider import normalize_yfinance_symbol
from app.services.market_calendar import IST, last_completed_trading_day
from app.services.ohlcv_utils import finite_decimal
from app.services.recommendation_engine import StockRecommendation, all_report_recommendations
from nsefeed.utils import get_previous_trading_day, is_trading_day

NSE_SESSION_OPEN = time(9, 15)
NSE_SESSION_CLOSE = time(15, 30)

# Finest fetch interval; larger intervals are resampled in-memory (no extra API calls).
BASE_INTERVAL_MINUTES = 5

INTERVAL_OPTIONS: dict[str, int] = {
    "5m": 5,
    "10m": 10,
    "15m": 15,
    "30m": 30,
    "1h": 60,
}

DEFAULT_INTERVAL = "15m"


@dataclass
class IntradayBar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass
class PositionIntradayContext:
    symbol: str
    pattern_name: str | None = None
    prev_close: float | None = None
    today_open: float | None = None
    today_high: float | None = None
    today_low: float | None = None
    current_price: float | None = None
    target_price: float | None = None
    stop_loss_price: float | None = None
    model_target_price: float | None = None
    resistance: float | None = None
    entry_price: float | None = None
    bars: list[IntradayBar] = field(default_factory=list)
    data_source: str = "yfinance"


def _finite(value) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return round(f, 2)


def _fetch_nse_day_quote(symbol: str) -> dict[str, float | None]:
    """Live day OHLC from NSE quote API."""
    try:
        info = nf.Ticker(symbol.upper()).info
    except Exception:
        return {}
    if info.get("error"):
        return {}
    return {
        "prev_close": _finite(info.get("previousClose")),
        "open": _finite(info.get("open")),
        "high": _finite(info.get("dayHigh")),
        "low": _finite(info.get("dayLow")),
        "last": _finite(info.get("lastPrice")),
    }


def fetch_session_ohlc_sync(symbol: str) -> dict[str, float | None | str]:
    """Today's session OHLC — NSE quote first, yfinance intraday bars as fallback."""
    quote = _fetch_nse_day_quote(symbol)
    if quote.get("high") is not None and quote.get("low") is not None:
        return {**quote, "source": "nse"}

    bars = _fetch_intraday_bars_sync(symbol)
    if bars:
        return {
            "open": bars[0].open,
            "high": max(b.high for b in bars),
            "low": min(b.low for b in bars),
            "last": bars[-1].close,
            "prev_close": quote.get("prev_close"),
            "source": "yfinance_intraday",
        }

    return {**quote, "source": "none"}


def _fetch_intraday_bars_sync(symbol: str) -> list[IntradayBar]:
    """5-minute intraday bars for today (IST session) — base series for resampling."""
    try:
        yf_symbol = normalize_yfinance_symbol(symbol)
        df = yf.Ticker(yf_symbol).history(period="1d", interval=f"{BASE_INTERVAL_MINUTES}m")
    except Exception:
        return []
    if df.empty:
        return []

    today = datetime.now(IST).date()
    bars: list[IntradayBar] = []
    for idx, row in df.iterrows():
        ts = idx.to_pydatetime()
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=IST)
        else:
            ts = ts.astimezone(IST)
        if ts.date() != today:
            continue
        t = ts.time()
        if t < NSE_SESSION_OPEN or t > NSE_SESSION_CLOSE:
            continue
        bars.append(
            IntradayBar(
                timestamp=ts,
                open=round(float(row["Open"]), 2),
                high=round(float(row["High"]), 2),
                low=round(float(row["Low"]), 2),
                close=round(float(row["Close"]), 2),
            )
        )
    return bars


def resample_intraday_bars(bars: list[IntradayBar], interval_minutes: int) -> list[IntradayBar]:
    """Aggregate 5m bars into wider candles without refetching market data."""
    if interval_minutes <= BASE_INTERVAL_MINUTES or len(bars) <= 1:
        return bars

    import pandas as pd

    frame = pd.DataFrame(
        {
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
        },
        index=pd.DatetimeIndex([b.timestamp for b in bars]),
    )
    frame = frame.sort_index()
    ohlc = (
        frame.resample(f"{interval_minutes}min", label="left", closed="left")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna(how="any")
    )

    resampled: list[IntradayBar] = []
    for ts, row in ohlc.iterrows():
        dt = ts.to_pydatetime()
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        else:
            dt = dt.astimezone(IST)
        resampled.append(
            IntradayBar(
                timestamp=dt,
                open=round(float(row["open"]), 2),
                high=round(float(row["high"]), 2),
                low=round(float(row["low"]), 2),
                close=round(float(row["close"]), 2),
            )
        )
    return resampled


def _synthetic_bars_from_quote(quote: dict[str, float | None]) -> list[IntradayBar]:
    """Minimal two-point line when minute data is unavailable."""
    open_ = quote.get("open")
    last = quote.get("last") or quote.get("high") or quote.get("low")
    if open_ is None or last is None:
        return []

    now = datetime.now(IST)
    session_open = datetime.combine(now.date(), NSE_SESSION_OPEN, tzinfo=IST)
    return [
        IntradayBar(session_open, open_, open_, open_, open_),
        IntradayBar(now, last, last, last, last),
    ]


async def fetch_intraday_bars(symbol: str) -> list[IntradayBar]:
    bars = await asyncio.to_thread(_fetch_intraday_bars_sync, symbol)
    if bars:
        return bars
    quote = await asyncio.to_thread(_fetch_nse_day_quote, symbol)
    return _synthetic_bars_from_quote(quote)


async def _prior_session_close_from_db(
    session: AsyncSession,
    instrument_id: int,
    *,
    as_of: date | None = None,
) -> float | None:
    """Previous trading session EOD close from synced market-data (ohlcv_candles)."""
    reference = as_of or datetime.now(IST).date()
    if is_trading_day(reference):
        prior_date = get_previous_trading_day(reference)
    else:
        prior_date = last_completed_trading_day()

    candle = await session.scalar(
        select(OhlcvCandle).where(
            OhlcvCandle.instrument_id == instrument_id,
            OhlcvCandle.trade_date == prior_date,
        )
    )
    if candle is not None:
        close = finite_decimal(candle.close)
        if close is not None:
            return round(float(close), 2)

    fallback = await session.scalar(
        select(OhlcvCandle)
        .where(
            OhlcvCandle.instrument_id == instrument_id,
            OhlcvCandle.trade_date < reference,
        )
        .order_by(OhlcvCandle.trade_date.desc())
        .limit(1)
    )
    if fallback is None:
        return None
    close = finite_decimal(fallback.close)
    return round(float(close), 2) if close is not None else None


async def _prev_close_from_db(session: AsyncSession, instrument_id: int, today: date) -> float | None:
    """Backward-compatible alias."""
    return await _prior_session_close_from_db(session, instrument_id, as_of=today)


async def _recommendation_for_symbol(
    session: AsyncSession,
    symbol: str,
) -> tuple[StockRecommendation | None, object | None]:
    from app.services.recommendation_cache import find_recommendation_for_symbol, load_cached_recommendations

    sym = symbol.upper()
    cached = await load_cached_recommendations()
    if cached:
        report, allocation, _, _, _ = cached
        for rec in all_report_recommendations(report):
            if rec.symbol.upper() == sym:
                line = next((ln for ln in allocation.lines if ln.symbol.upper() == sym), None)
                return rec, line

    return await find_recommendation_for_symbol(session, sym)


async def build_position_intraday_context(
    session: AsyncSession,
    symbol: str,
    *,
    live_price: float | None = None,
    mark_price: float | None = None,
) -> PositionIntradayContext:
    sym = symbol.upper()
    instrument = await session.scalar(select(Instrument).where(Instrument.symbol == sym))

    if instrument is not None:
        from app.services.ingestion import backfill_symbol_if_missing

        try:
            await backfill_symbol_if_missing(session, sym)
        except Exception:
            pass

    plan = await session.scalar(
        select(PaperTradePlan)
        .join(Instrument)
        .where(
            Instrument.symbol == sym,
            PaperTradePlan.status.in_(
                (TradePlanStatus.PENDING_ENTRY, TradePlanStatus.OPEN)
            ),
        )
        .options(selectinload(PaperTradePlan.instrument))
        .order_by(PaperTradePlan.recommendation_date.desc())
        .limit(1)
    )
    if plan is None:
        plan = await session.scalar(
            select(PaperTradePlan)
            .join(Instrument)
            .where(Instrument.symbol == sym)
            .options(selectinload(PaperTradePlan.instrument))
            .order_by(PaperTradePlan.recommendation_date.desc())
            .limit(1)
        )

    rec, alloc_line = await _recommendation_for_symbol(session, sym)
    try:
        quote = await asyncio.to_thread(_fetch_nse_day_quote, sym)
    except Exception:
        quote = {}
    try:
        bars = await fetch_intraday_bars(sym)
    except Exception:
        bars = []

    today = datetime.now(IST).date()
    prev_close = None
    if instrument is not None:
        prev_close = await _prior_session_close_from_db(session, instrument.id, as_of=today)
    if prev_close is None:
        prev_close = quote.get("prev_close")
    if prev_close is None and rec is not None:
        prev_close = _finite(rec.prev_close)
    if prev_close is None and mark_price is not None:
        prev_close = _finite(mark_price)

    today_open = quote.get("open")
    today_high = quote.get("high")
    today_low = quote.get("low")
    if bars:
        today_high = today_high or max(b.high for b in bars)
        today_low = today_low or min(b.low for b in bars)
        if today_open is None:
            today_open = bars[0].open

    current = live_price
    if current is None:
        current = quote.get("last")
    if current is None and bars:
        current = bars[-1].close

    target = _finite(plan.target_price) if plan else None
    stop = _finite(plan.stop_loss_price) if plan else None
    entry = _finite(plan.entry_price or plan.entry_limit_price) if plan else None
    pattern_name = (plan.pattern_name if plan else None)
    if not pattern_name and rec is not None:
        pattern_name = rec.pattern_name
    if not pattern_name and alloc_line is not None:
        pattern_name = getattr(alloc_line, "pattern_name", None)
    if not pattern_name and plan is None and rec is None:
        pattern_name = None

    model_target = _finite(rec.sell_price) if rec else None
    resistance = _finite(rec.resistance) if rec else None
    if rec:
        stop = stop or _finite(rec.stop_loss)
        target = target or _finite(rec.actual_sell_price)
    if alloc_line is not None:
        stop = stop or _finite(getattr(alloc_line, "stop_loss", None))
        target = target or _finite(getattr(alloc_line, "actual_sell_price", None))
        model_target = model_target or _finite(getattr(alloc_line, "model_target_price", None))

    data_source = "yfinance" if len(bars) > 2 else "nse_quote"

    return PositionIntradayContext(
        symbol=sym,
        pattern_name=pattern_name,
        prev_close=prev_close,
        today_open=today_open,
        today_high=today_high,
        today_low=today_low,
        current_price=_finite(current),
        target_price=target,
        stop_loss_price=stop,
        model_target_price=model_target,
        resistance=resistance,
        entry_price=entry,
        bars=bars,
        data_source=data_source,
    )

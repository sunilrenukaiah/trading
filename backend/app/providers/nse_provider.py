"""NSE India market data via official bhavcopy archives and quote API.

On Cloud / blocked IPs, NSE often returns HTTP 403. This provider tries NSE
first, then falls back to Yahoo Finance for the same symbol/date range.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from decimal import Decimal

import nsefeed as nf
from nsefeed.exceptions import NSEDataNotFoundError, NSEConnectionError
from nsefeed.scrapers.bhav_copy import BhavCopyScraper
from nsefeed.utils import is_trading_day

from app.providers.base import CandleData, MarketDataProvider, QuoteData
from app.providers.yfinance_provider import YFinanceProvider, normalize_yfinance_symbol
from app.services.ohlcv_utils import valid_candle_prices

log = logging.getLogger(__name__)

_INDEX_YFINANCE = {"^NSEI", "NIFTY50"}

# Process-wide sticky switch after NSE access is clearly blocked (e.g. Cloud 403).
_NSE_FORCE_YFINANCE = False


def reset_nse_yfinance_fallback_for_tests() -> None:
    """Test helper — clear sticky fallback."""
    global _NSE_FORCE_YFINANCE
    _NSE_FORCE_YFINANCE = False


def _is_nse_access_failure(exc: BaseException) -> bool:
    """True for blocked/forbidden NSE sessions (typical on Streamlit Cloud)."""
    if isinstance(exc, NSEConnectionError):
        return True
    msg = str(exc).lower()
    markers = (
        "403",
        "forbidden",
        "failed to establish session",
        "access denied",
        "blocked",
        "too many requests",
        "429",
        "nseindia",
        "nse connection",
    )
    if any(m in msg for m in markers):
        return True
    cause = exc.__cause__ or exc.__context__
    if cause is not None and cause is not exc:
        return _is_nse_access_failure(cause)
    name = type(exc).__name__.lower()
    return "http" in name and ("403" in msg or "forbidden" in msg)


def _activate_yfinance_fallback(reason: BaseException | str) -> None:
    global _NSE_FORCE_YFINANCE
    if not _NSE_FORCE_YFINANCE:
        log.warning("NSE unavailable (%s); falling back to yfinance", reason)
    _NSE_FORCE_YFINANCE = True


def nse_forced_to_yfinance() -> bool:
    return _NSE_FORCE_YFINANCE


def probe_nse_session(*, trading_day: date | None = None) -> bool:
    """
    Light NSE reachability check. On failure, sticky-switch to yfinance.

    Returns True when NSE responded; False when fallback was activated.
    """
    if _NSE_FORCE_YFINANCE:
        return False
    day = trading_day or date.today()
    scraper = BhavCopyScraper(use_cache=True)
    # Walk back a few calendar days to find a session file worth probing.
    for _ in range(10):
        if is_trading_day(day):
            try:
                scraper.fetch_for_date(day)
                return True
            except Exception as exc:
                _activate_yfinance_fallback(exc)
                return False
        day -= timedelta(days=1)
    _activate_yfinance_fallback("no recent NSE trading day to probe")
    return False


def _yfinance_index_symbol(market_symbol: str) -> str:
    symbol = market_symbol.strip().upper()
    if symbol in _INDEX_YFINANCE:
        return "^NSEI"
    return symbol


def normalize_nse_symbol(market_symbol: str) -> tuple[str, bool]:
    """Return (nse_symbol, is_index). Accepts TCS, TCS.NS, or ^NSEI."""
    symbol = market_symbol.strip().upper()
    if symbol in _INDEX_YFINANCE or symbol.startswith("^"):
        return symbol, True
    return symbol.removesuffix(".NS"), False


class NSEProvider(MarketDataProvider):
    """
    Fetches EOD OHLCV from NSE bhavcopy (archives.nseindia.com / nsearchives.nseindia.com).

    Uses the official NSE **closing price** (weighted average of last 30 minutes),
    not the last-traded price (LTP) shown on some consumer sites during/after the session.

    If NSE session/API fails (403 from Cloud IPs, etc.), subsequent fetches use yfinance.
    """

    def __init__(self) -> None:
        self._scraper = BhavCopyScraper(use_cache=True)
        self._yf_fallback = YFinanceProvider()

    def _yf_candles(self, market_symbol: str, start: date, end: date) -> list[CandleData]:
        return self._yf_fallback._fetch_candles_sync(
            normalize_yfinance_symbol(market_symbol), start, end
        )

    def _yf_quotes(self, market_symbols: list[str]) -> dict[str, QuoteData]:
        return self._yf_fallback._fetch_quotes_sync(market_symbols)

    async def fetch_candles(
        self, yfinance_symbol: str, start: date, end: date
    ) -> list[CandleData]:
        return await asyncio.to_thread(
            self._fetch_candles_sync, yfinance_symbol, start, end
        )

    def _fetch_nse_equity_candles(
        self, symbol: str, start: date, end: date
    ) -> list[CandleData]:
        candles: list[CandleData] = []
        current = start

        while current <= end:
            if is_trading_day(current):
                try:
                    daily = self._scraper.fetch_for_date(current)
                    rows = daily[daily["symbol"] == symbol]
                    if not rows.empty:
                        row = rows.iloc[0]
                        open_ = Decimal(str(round(float(row["open"]), 4)))
                        high = Decimal(str(round(float(row["high"]), 4)))
                        low = Decimal(str(round(float(row["low"]), 4)))
                        close = Decimal(str(round(float(row["close"]), 4)))
                        if valid_candle_prices(open_, high, low, close):
                            candles.append(
                                CandleData(
                                    trade_date=current,
                                    open=open_,
                                    high=high,
                                    low=low,
                                    close=close,
                                    volume=int(row["volume"])
                                    if row.get("volume") == row.get("volume")
                                    else 0,
                                )
                            )
                except (NSEDataNotFoundError, NSEConnectionError) as exc:
                    # Connection errors are usually Cloud IP blocks — fail over immediately.
                    if isinstance(exc, NSEConnectionError) or _is_nse_access_failure(exc):
                        _activate_yfinance_fallback(exc)
                        raise
                except Exception as exc:
                    # Any unexpected NSE/scraper failure → yfinance (do not abort the sync job).
                    _activate_yfinance_fallback(exc)
                    raise
            current += timedelta(days=1)

        return candles

    def _fetch_candles_sync(
        self, market_symbol: str, start: date, end: date
    ) -> list[CandleData]:
        symbol, is_index = normalize_nse_symbol(market_symbol)
        if is_index:
            return self._yf_fallback._fetch_candles_sync(
                _yfinance_index_symbol(market_symbol), start, end
            )

        if _NSE_FORCE_YFINANCE:
            return self._yf_candles(symbol, start, end)

        try:
            candles = self._fetch_nse_equity_candles(symbol, start, end)
        except Exception as exc:
            # Prefer Yahoo over failing the whole market-sync job.
            _activate_yfinance_fallback(exc)
            return self._yf_candles(symbol, start, end)

        # Silent per-day failures (empty over a wide window) → try Yahoo once.
        if not candles and (end - start).days >= 5:
            yf_candles = self._yf_candles(symbol, start, end)
            if yf_candles:
                _activate_yfinance_fallback("NSE returned no candles; yfinance has data")
                return yf_candles
        return candles

    async def fetch_latest_quotes(self, yfinance_symbols: list[str]) -> dict[str, QuoteData]:
        return await asyncio.to_thread(self._fetch_quotes_sync, yfinance_symbols)

    def _fetch_quotes_sync(self, market_symbols: list[str]) -> dict[str, QuoteData]:
        if _NSE_FORCE_YFINANCE:
            return self._yf_quotes(market_symbols)

        quotes: dict[str, QuoteData] = {}
        pending_yf: list[str] = []

        for market_symbol in market_symbols:
            if _NSE_FORCE_YFINANCE:
                pending_yf.append(market_symbol)
                continue

            symbol, is_index = normalize_nse_symbol(market_symbol)
            if is_index:
                quotes.update(self._yf_quotes([market_symbol]))
                continue

            try:
                info = nf.Ticker(symbol).info
                last = info.get("lastPrice")
                prev = info.get("previousClose")
                day_high = info.get("dayHigh")
                day_low = info.get("dayLow")
                day_open = info.get("open")
                if last is not None:
                    quotes[market_symbol] = QuoteData(
                        symbol=market_symbol,
                        last_price=Decimal(str(round(float(last), 4))),
                        prev_close=Decimal(str(round(float(prev), 4))) if prev else None,
                        day_open=Decimal(str(round(float(day_open), 4)))
                        if day_open is not None
                        else None,
                        day_high=Decimal(str(round(float(day_high), 4)))
                        if day_high is not None
                        else None,
                        day_low=Decimal(str(round(float(day_low), 4)))
                        if day_low is not None
                        else None,
                    )
                    continue
            except Exception as exc:
                if _is_nse_access_failure(exc):
                    _activate_yfinance_fallback(exc)
                    pending_yf.append(market_symbol)
                    continue

            end = date.today()
            start = end - timedelta(days=7)
            try:
                candles = self._fetch_candles_sync(symbol, start, end)
            except Exception:
                candles = []
            if candles:
                last_c = candles[-1]
                prev_c = candles[-2].close if len(candles) > 1 else None
                quotes[market_symbol] = QuoteData(
                    symbol=market_symbol,
                    last_price=last_c.close,
                    prev_close=prev_c,
                    day_open=last_c.open,
                    day_high=last_c.high,
                    day_low=last_c.low,
                )
            else:
                pending_yf.append(market_symbol)

        if _NSE_FORCE_YFINANCE:
            missing = [s for s in market_symbols if s not in quotes]
            for sym, quote in self._yf_quotes(missing).items():
                quotes[sym] = quote
        elif pending_yf:
            for sym, quote in self._yf_quotes(pending_yf).items():
                quotes.setdefault(sym, quote)
        return quotes

    async def sync_instrument_master(self, exchange: str = "NC") -> list[dict]:
        raise NotImplementedError("Use static NIFTY 50 seed for NSE provider")

"""NSE India market data via official bhavcopy archives and quote API."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from decimal import Decimal

import nsefeed as nf
from nsefeed.exceptions import NSEDataNotFoundError, NSEConnectionError
from nsefeed.scrapers.bhav_copy import BhavCopyScraper
from nsefeed.utils import is_trading_day

from app.providers.base import CandleData, MarketDataProvider, QuoteData
from app.providers.yfinance_provider import YFinanceProvider
from app.services.ohlcv_utils import valid_candle_prices

_INDEX_YFINANCE = {"^NSEI", "NIFTY50"}


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
    """

    def __init__(self) -> None:
        self._scraper = BhavCopyScraper(use_cache=True)
        self._yf_fallback = YFinanceProvider()

    async def fetch_candles(
        self, yfinance_symbol: str, start: date, end: date
    ) -> list[CandleData]:
        return await asyncio.to_thread(
            self._fetch_candles_sync, yfinance_symbol, start, end
        )

    def _fetch_candles_sync(
        self, market_symbol: str, start: date, end: date
    ) -> list[CandleData]:
        symbol, is_index = normalize_nse_symbol(market_symbol)
        if is_index:
            return self._yf_fallback._fetch_candles_sync(
                _yfinance_index_symbol(market_symbol), start, end
            )

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
                        if not valid_candle_prices(open_, high, low, close):
                            pass
                        else:
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
                except (NSEDataNotFoundError, NSEConnectionError):
                    pass
            current += timedelta(days=1)

        return candles

    async def fetch_latest_quotes(self, yfinance_symbols: list[str]) -> dict[str, QuoteData]:
        return await asyncio.to_thread(self._fetch_quotes_sync, yfinance_symbols)

    def _fetch_quotes_sync(self, market_symbols: list[str]) -> dict[str, QuoteData]:
        quotes: dict[str, QuoteData] = {}
        for market_symbol in market_symbols:
            symbol, is_index = normalize_nse_symbol(market_symbol)
            if is_index:
                quotes.update(self._yf_fallback._fetch_quotes_sync([market_symbol]))
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
            except Exception:
                pass

            end = date.today()
            start = end - timedelta(days=7)
            candles = self._fetch_candles_sync(symbol, start, end)
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
        return quotes

    async def sync_instrument_master(self, exchange: str = "NC") -> list[dict]:
        raise NotImplementedError("Use static NIFTY 50 seed for NSE provider")

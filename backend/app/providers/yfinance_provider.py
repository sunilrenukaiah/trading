import asyncio
from datetime import date, timedelta
from decimal import Decimal

import yfinance as yf

from app.providers.base import CandleData, MarketDataProvider, QuoteData

_INDEX_YFINANCE = {"^NSEI", "NIFTY50"}


def normalize_yfinance_symbol(market_symbol: str) -> str:
    """Map NSE symbols to Yahoo tickers (e.g. TCS -> TCS.NS, NIFTY50 -> ^NSEI)."""
    symbol = market_symbol.strip().upper()
    if symbol.startswith("^") or symbol.endswith(".NS") or symbol.endswith(".BO"):
        return symbol
    if symbol in _INDEX_YFINANCE:
        return "^NSEI"
    return f"{symbol}.NS"


class YFinanceProvider(MarketDataProvider):
    async def fetch_candles(
        self, yfinance_symbol: str, start: date, end: date
    ) -> list[CandleData]:
        return await asyncio.to_thread(self._fetch_candles_sync, yfinance_symbol, start, end)

    def _fetch_candles_sync(
        self, yfinance_symbol: str, start: date, end: date
    ) -> list[CandleData]:
        yf_symbol = normalize_yfinance_symbol(yfinance_symbol)
        ticker = yf.Ticker(yf_symbol)
        # yfinance end date is exclusive
        df = ticker.history(start=start.isoformat(), end=(end + timedelta(days=1)).isoformat())
        if df.empty:
            return []

        candles: list[CandleData] = []
        for idx, row in df.iterrows():
            trade_date = idx.date() if hasattr(idx, "date") else idx
            candles.append(
                CandleData(
                    trade_date=trade_date,
                    open=Decimal(str(round(row["Open"], 4))),
                    high=Decimal(str(round(row["High"], 4))),
                    low=Decimal(str(round(row["Low"], 4))),
                    close=Decimal(str(round(row["Close"], 4))),
                    volume=int(row["Volume"]) if row["Volume"] == row["Volume"] else 0,
                )
            )
        return candles

    async def fetch_latest_quotes(self, yfinance_symbols: list[str]) -> dict[str, QuoteData]:
        return await asyncio.to_thread(self._fetch_quotes_sync, yfinance_symbols)

    def _fetch_quotes_sync(self, yfinance_symbols: list[str]) -> dict[str, QuoteData]:
        quotes: dict[str, QuoteData] = {}
        for symbol in yfinance_symbols:
            yf_symbol = normalize_yfinance_symbol(symbol)
            ticker = yf.Ticker(yf_symbol)
            hist = ticker.history(period="5d")
            if hist.empty:
                continue
            last_close = Decimal(str(round(hist["Close"].iloc[-1], 4)))
            prev_close = (
                Decimal(str(round(hist["Close"].iloc[-2], 4))) if len(hist) > 1 else None
            )
            last_row = hist.iloc[-1]
            quotes[symbol] = QuoteData(
                symbol=symbol,
                last_price=last_close,
                prev_close=prev_close,
                day_open=Decimal(str(round(last_row["Open"], 4))),
                day_high=Decimal(str(round(last_row["High"], 4))),
                day_low=Decimal(str(round(last_row["Low"], 4))),
            )
        return quotes

    async def sync_instrument_master(self, exchange: str = "NC") -> list[dict]:
        raise NotImplementedError("Use static NIFTY 50 seed for yfinance provider")

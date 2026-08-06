from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass
class CandleData:
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


@dataclass
class QuoteData:
    symbol: str
    last_price: Decimal
    prev_close: Decimal | None = None
    day_open: Decimal | None = None
    day_high: Decimal | None = None
    day_low: Decimal | None = None

    @property
    def session_high(self) -> Decimal:
        if self.day_high is not None:
            return max(self.day_high, self.last_price)
        return self.last_price

    @property
    def session_low(self) -> Decimal:
        if self.day_low is not None:
            return min(self.day_low, self.last_price)
        return self.last_price


@dataclass(frozen=True)
class SessionQuote:
    """LTP snapshot for bracket checks; poll_high/poll_low are accumulated across live polls."""

    last_price: Decimal
    poll_high: Decimal | None = None
    poll_low: Decimal | None = None
    day_open: Decimal | None = None
    prev_close: Decimal | None = None
    nse_day_high: Decimal | None = None
    nse_day_low: Decimal | None = None

    @property
    def observed_high(self) -> Decimal:
        high = self.last_price
        if self.poll_high is not None:
            high = max(high, self.poll_high)
        if self.nse_day_high is not None:
            high = max(high, self.nse_day_high)
        return high

    @property
    def observed_low(self) -> Decimal:
        low = self.last_price
        if self.poll_low is not None:
            low = min(low, self.poll_low)
        if self.nse_day_low is not None:
            low = min(low, self.nse_day_low)
        return low


class MarketDataProvider(ABC):
    @abstractmethod
    async def fetch_candles(
        self, yfinance_symbol: str, start: date, end: date
    ) -> list[CandleData]:
        ...

    @abstractmethod
    async def fetch_latest_quotes(self, yfinance_symbols: list[str]) -> dict[str, QuoteData]:
        ...

    @abstractmethod
    async def sync_instrument_master(self, exchange: str = "NC") -> list[dict]:
        ...

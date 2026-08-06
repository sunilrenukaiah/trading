from datetime import date

from app.providers.base import CandleData, MarketDataProvider, QuoteData


class SharekhanProvider(MarketDataProvider):
    """Phase 2 stub — implement when Sharekhan API access is enabled."""

    def __init__(self, api_key: str, customer_id: str, access_token: str | None = None):
        self.api_key = api_key
        self.customer_id = customer_id
        self.access_token = access_token

    async def fetch_candles(
        self, yfinance_symbol: str, start: date, end: date
    ) -> list[CandleData]:
        raise NotImplementedError(
            "Sharekhan historicaldata() integration pending. "
            "Enable API at https://www.sharekhan.com/trading-api and install shareconnect."
        )

    async def fetch_latest_quotes(self, yfinance_symbols: list[str]) -> dict[str, QuoteData]:
        raise NotImplementedError("Sharekhan WebSocket/market feed integration pending.")

    async def sync_instrument_master(self, exchange: str = "NC") -> list[dict]:
        raise NotImplementedError(
            "Sharekhan master(exchange) integration pending. "
            "Will map scrip codes to instruments.sharekhan_scrip_code."
        )

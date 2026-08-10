from app.config import settings
from app.providers.base import MarketDataProvider
from app.providers.nse_provider import NSEProvider
from app.providers.sharekhan_provider import SharekhanProvider
from app.providers.yfinance_provider import YFinanceProvider


def get_market_data_provider() -> MarketDataProvider:
    """Return the configured provider.

    Default ``nse`` tries NSE first and falls back to yfinance on 403/block
    (common on Streamlit Cloud). Set ``DATA_PROVIDER=yfinance`` to skip NSE.
    """
    if settings.data_provider == "sharekhan":
        if not settings.sharekhan_api_key or not settings.sharekhan_customer_id:
            raise ValueError("Sharekhan API credentials not configured")
        return SharekhanProvider(
            api_key=settings.sharekhan_api_key,
            customer_id=settings.sharekhan_customer_id,
            access_token=settings.sharekhan_access_token,
        )
    if settings.data_provider == "yfinance":
        return YFinanceProvider()
    return NSEProvider()

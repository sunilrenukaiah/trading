"""Live quotes and market session tests."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.market_calendar import IST, is_live_quote_session

IST_DT = lambda y, m, d, h, mi=0: datetime(y, m, d, h, mi, tzinfo=IST)


@pytest.mark.quick
def test_live_quote_session_during_market_hours() -> None:
    now = IST_DT(2026, 7, 30, 11, 0)
    assert is_live_quote_session(now=now) is True


@pytest.mark.quick
def test_live_quote_session_closed_evening() -> None:
    now = IST_DT(2026, 7, 30, 18, 0)
    assert is_live_quote_session(now=now) is False


@pytest.mark.quick
@pytest.mark.asyncio
async def test_fetch_live_quotes_maps_symbols() -> None:
    from app.services.live_quotes import fetch_live_quotes
    from app.providers.base import QuoteData, SessionQuote

    inst = MagicMock()
    inst.symbol = "INFY"
    inst.yfinance_symbol = "INFY.NS"

    session = AsyncMock()

    class _ScalarResult:
        def all(self):
            return [inst]

    session.scalars = AsyncMock(return_value=_ScalarResult())

    class _Provider:
        async def fetch_latest_quotes(self, symbols):
            assert symbols == ["INFY.NS"]
            return {
                "INFY.NS": QuoteData(
                    symbol="INFY.NS",
                    last_price=Decimal("1500.25"),
                    day_high=Decimal("1510"),
                    day_low=Decimal("1490"),
                )
            }

    import app.services.live_quotes as lq

    original = lq.get_market_data_provider
    lq.get_market_data_provider = lambda: _Provider()
    try:
        quotes = await fetch_live_quotes(session, ["infy"])
    finally:
        lq.get_market_data_provider = original

    assert quotes["INFY"].last_price == Decimal("1500.25")
    assert quotes["INFY"].nse_day_high == Decimal("1510")
    assert quotes["INFY"].poll_high == Decimal("1510")
    assert quotes["INFY"].poll_low == Decimal("1500.25")


@pytest.mark.quick
def test_quote_field_tolerates_stale_quote_data() -> None:
    from types import SimpleNamespace

    from app.services.live_quotes import _quote_field

    stale = SimpleNamespace(last_price=Decimal("100"), prev_close=Decimal("99"))
    assert _quote_field(stale, "day_open") is None
    assert _quote_field(stale, "prev_close") == Decimal("99")


@pytest.mark.quick
def test_merge_poll_extremes_accumulates_across_cycles() -> None:
    from app.providers.base import SessionQuote
    from app.services.live_quotes import merge_poll_extremes, reset_poll_extremes

    reset_poll_extremes()
    first = merge_poll_extremes({"INFY": SessionQuote(last_price=Decimal("1500"))})
    second = merge_poll_extremes({"INFY": SessionQuote(last_price=Decimal("1510"))})
    third = merge_poll_extremes({"INFY": SessionQuote(last_price=Decimal("1495"))})

    assert first["INFY"].poll_high == Decimal("1500")
    assert second["INFY"].poll_high == Decimal("1510")
    assert second["INFY"].poll_low == Decimal("1500")
    assert third["INFY"].poll_high == Decimal("1510")
    assert third["INFY"].poll_low == Decimal("1495")
    reset_poll_extremes()


@pytest.mark.quick
@pytest.mark.asyncio
async def test_market_sell_uses_live_fill_price() -> None:
    from app.models import OrderSide, OrderType
    from app.schemas import PlaceOrderRequest
    from app.services.paper_trading import PaperTradingService

    service = PaperTradingService(AsyncMock())
    service.get_default_account = AsyncMock(return_value=MagicMock(id=1, cash_balance=Decimal("0")))
    service._get_instrument = AsyncMock(return_value=MagicMock(id=10))
    service._fill_order = AsyncMock()

    await service.place_order(
        PlaceOrderRequest(
            symbol="INFY",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=5,
        ),
        market_fill_price=Decimal("1500"),
    )

    service._fill_order.assert_awaited_once()
    assert service._fill_order.await_args.kwargs["fill_price"] == Decimal("1500")

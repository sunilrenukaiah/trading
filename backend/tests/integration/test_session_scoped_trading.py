"""Session-scoped orders/trades and recommendation symbol lookup."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from app.models import OrderSide, OrderStatus, OrderType
from app.services.market_calendar import IST, current_session_date
from app.services.paper_trading import PaperTradingService


@pytest.mark.quick
@pytest.mark.asyncio
async def test_list_orders_filters_by_session_date() -> None:
    service = PaperTradingService(AsyncMock())
    service.get_default_account = AsyncMock(return_value=MagicMock(id=1))

    instrument = MagicMock()
    instrument.symbol = "ITC"
    today = datetime(2026, 7, 30, 10, 0, tzinfo=IST)
    yesterday = datetime(2026, 7, 29, 10, 0, tzinfo=IST)

    order_today = MagicMock(
        id=1,
        instrument=instrument,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=10,
        limit_price=None,
        status=OrderStatus.FILLED,
        filled_price=Decimal("285"),
        filled_at=today,
        created_at=today,
    )
    order_yesterday = MagicMock(
        id=2,
        instrument=instrument,
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        quantity=10,
        limit_price=None,
        status=OrderStatus.FILLED,
        filled_price=Decimal("284"),
        filled_at=yesterday,
        created_at=yesterday,
    )

    service.session.scalars = AsyncMock(
        return_value=MagicMock(all=MagicMock(return_value=[order_today, order_yesterday]))
    )

    rows = await service.list_orders(session_date=date(2026, 7, 30))
    assert len(rows) == 1
    assert rows[0].id == 1


@pytest.mark.quick
def test_current_session_date_uses_ist() -> None:
    now = datetime(2026, 7, 30, 18, 0, tzinfo=timezone.utc)
    assert current_session_date(now=now) == date(2026, 7, 30)

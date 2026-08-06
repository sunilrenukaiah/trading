"""Paper trading square-off and position source tests."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from app.models import OrderStatus
from app.schemas import PositionSource
from app.services.paper_trading import PaperTradingService


@pytest.mark.quick
@pytest.mark.asyncio
async def test_square_off_remaining_positions_at_325pm() -> None:
    service = PaperTradingService(AsyncMock())
    service.get_default_account = AsyncMock(return_value=MagicMock(id=1))
    service.symbols_with_open_bracket_plans = AsyncMock(return_value=set())

    instrument = MagicMock()
    instrument.symbol = "ITC"
    position = MagicMock()
    position.instrument = instrument
    position.quantity = 18

    service.session.scalars = AsyncMock(
        return_value=MagicMock(all=MagicMock(return_value=[position]))
    )

    filled_order = MagicMock(status=OrderStatus.FILLED)
    service.place_order = AsyncMock(return_value=filled_order)

    at_square_off = datetime(2026, 7, 30, 15, 26, tzinfo=ZoneInfo("Asia/Kolkata"))
    closed = await service.square_off_remaining_positions(
        {"ITC": Decimal("285.05")}, now=at_square_off
    )

    assert closed == 1
    service.place_order.assert_awaited_once()
    req = service.place_order.await_args.args[0]
    assert req.symbol == "ITC"
    assert req.quantity == 18


@pytest.mark.quick
@pytest.mark.asyncio
async def test_square_off_remaining_skipped_before_325pm() -> None:
    service = PaperTradingService(AsyncMock())
    service.place_order = AsyncMock()

    before = datetime(2026, 7, 30, 11, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    closed = await service.square_off_remaining_positions(
        {"ITC": Decimal("285")}, now=before
    )

    assert closed == 0
    service.place_order.assert_not_awaited()


@pytest.mark.quick
@pytest.mark.asyncio
async def test_list_positions_marks_recommendation_source() -> None:
    service = PaperTradingService(AsyncMock())
    service.get_default_account = AsyncMock(return_value=MagicMock(id=1))
    service.recommendation_position_symbols = AsyncMock(return_value={"INFY"})
    service._latest_close = AsyncMock(return_value=Decimal("1500"))

    instrument = MagicMock()
    instrument.symbol = "INFY"
    instrument.name = "Infosys"
    pos = MagicMock()
    pos.instrument = instrument
    pos.instrument_id = 1
    pos.quantity = 10
    pos.avg_cost = Decimal("1480")

    service.session.scalars = AsyncMock(
        return_value=MagicMock(all=MagicMock(return_value=[pos]))
    )

    rows = await service.list_positions()
    assert len(rows) == 1
    assert rows[0].source == PositionSource.RECOMMENDATION


@pytest.mark.quick
@pytest.mark.asyncio
async def test_list_positions_marks_manual_source() -> None:
    service = PaperTradingService(AsyncMock())
    service.get_default_account = AsyncMock(return_value=MagicMock(id=1))
    service.recommendation_position_symbols = AsyncMock(return_value=set())
    service._latest_close = AsyncMock(return_value=Decimal("285"))

    instrument = MagicMock()
    instrument.symbol = "ITC"
    instrument.name = "ITC Ltd"
    pos = MagicMock()
    pos.instrument = instrument
    pos.instrument_id = 2
    pos.quantity = 18
    pos.avg_cost = Decimal("286.25")

    service.session.scalars = AsyncMock(
        return_value=MagicMock(all=MagicMock(return_value=[pos]))
    )

    rows = await service.list_positions()
    assert len(rows) == 1
    assert rows[0].source == PositionSource.MANUAL


@pytest.mark.quick
@pytest.mark.asyncio
async def test_square_off_skips_open_bracket_symbols() -> None:
    service = PaperTradingService(AsyncMock())
    service.get_default_account = AsyncMock(return_value=MagicMock(id=1))
    service.symbols_with_open_bracket_plans = AsyncMock(return_value={"ITC"})
    service.place_order = AsyncMock()

    instrument = MagicMock()
    instrument.symbol = "ITC"
    position = MagicMock()
    position.instrument = instrument
    position.quantity = 18

    service.session.scalars = AsyncMock(
        return_value=MagicMock(all=MagicMock(return_value=[position]))
    )

    at_square_off = datetime(2026, 7, 30, 15, 26, tzinfo=ZoneInfo("Asia/Kolkata"))
    closed = await service.square_off_remaining_positions(
        {"ITC": Decimal("285.05")}, now=at_square_off
    )

    assert closed == 0
    service.place_order.assert_not_awaited()


@pytest.mark.quick
@pytest.mark.asyncio
async def test_place_order_rejects_sell_for_bracket_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.models import OrderSide, OrderType
    from app.schemas import PlaceOrderRequest
    from ui import helpers

    service = AsyncMock()
    service.recommendation_position_symbols = AsyncMock(return_value={"INFY"})

    class FakeCtx:
        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(helpers, "ui_session", lambda: FakeCtx())
    monkeypatch.setattr(helpers, "PaperTradingService", lambda session: service)

    with pytest.raises(ValueError, match="active recommendation bracket"):
        await helpers._place_order(
            PlaceOrderRequest(
                symbol="INFY",
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=10,
            )
        )

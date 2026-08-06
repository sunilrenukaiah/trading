"""Bracket trade plan service tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models import Instrument, OrderSide, OrderStatus, OrderType, TradePlanStatus
from app.services.budget_allocator import AllocationLine
from app.services.trade_plans import TradePlanService


def _sample_line() -> AllocationLine:
    return AllocationLine(
        symbol="INFY",
        cap_tier="Large Cap",
        shares=10,
        buy_price=1500.0,
        investment=15000.0,
        stop_loss=1450.0,
        model_target_price=1600.0,
        actual_sell_price=1575.0,
        expected_profit=100.0,
        gross_profit=75.0,
        profit_before_tax=70.0,
        total_charges=5.0,
        stcg_tax=14.0,
        net_profit_after_tax=56.0,
        max_loss=500.0,
        weight_pct=30.0,
        pattern_name="Hammer",
        confidence_score=80.0,
    )


@pytest.mark.quick
@pytest.mark.asyncio
async def test_process_eod_stop_before_target(monkeypatch: pytest.MonkeyPatch) -> None:
    service = TradePlanService(AsyncMock())
    service.paper = AsyncMock()
    service.paper.match_pending_limit_orders = AsyncMock(return_value=0)
    service._sync_entries_from_orders = AsyncMock(return_value=0)
    service.paper.square_off_remaining_at_close = AsyncMock(return_value=0)

    plan = MagicMock()
    plan.instrument_id = 1
    plan.recommendation_date = date(2026, 7, 29)
    plan.instrument = MagicMock()
    plan.stop_loss_price = Decimal("1450")
    plan.target_price = Decimal("1575")
    plan.shares = 10

    candle = MagicMock()
    candle.low = Decimal("1440")
    candle.high = Decimal("1580")
    candle.close = Decimal("1500")

    service.session.scalars = AsyncMock(
        side_effect=[
            MagicMock(all=MagicMock(return_value=[plan])),
            MagicMock(all=MagicMock(return_value=[])),
        ]
    )
    service._candle_for_date = AsyncMock(return_value=candle)
    service._exit_plan = AsyncMock(return_value=True)

    stats = await service.process_eod(date(2026, 7, 29))

    assert stats["stops"] == 1
    assert stats["targets"] == 0
    service._exit_plan.assert_awaited_once_with(plan, plan.stop_loss_price, TradePlanStatus.STOP_HIT)


@pytest.mark.quick
@pytest.mark.asyncio
async def test_process_live_quotes_target_hit() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    service = TradePlanService(AsyncMock())
    service.paper = AsyncMock()

    plan = MagicMock()
    plan.instrument = MagicMock()
    plan.instrument.symbol = "INFY"
    plan.target_price = Decimal("1575")
    plan.stop_loss_price = Decimal("1450")
    plan.entry_limit_price = Decimal("1500")
    plan.shares = 10
    plan.status = TradePlanStatus.OPEN

    service.session.scalars = AsyncMock(
        side_effect=[
            MagicMock(all=MagicMock(return_value=[])),
            MagicMock(all=MagicMock(return_value=[plan])),
        ]
    )
    service._exit_plan = AsyncMock(return_value=True)

    mid_session = datetime(2026, 7, 30, 11, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    stats = await service.process_live_quotes(
        {"INFY": Decimal("1580")}, now=mid_session
    )

    assert stats["targets"] == 1
    service._exit_plan.assert_awaited_once()


@pytest.mark.quick
@pytest.mark.asyncio
async def test_process_live_quotes_target_hit_via_poll_high() -> None:
    """Target triggers when accumulated poll LTP high reached target even if current LTP is below."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.providers.base import SessionQuote

    service = TradePlanService(AsyncMock())
    service.paper = AsyncMock()

    plan = MagicMock()
    plan.instrument = MagicMock()
    plan.instrument.symbol = "JSWINFRA"
    plan.target_price = Decimal("330.54")
    plan.stop_loss_price = Decimal("310")
    plan.entry_limit_price = Decimal("320")
    plan.shares = 10
    plan.status = TradePlanStatus.OPEN

    service.session.scalars = AsyncMock(
        side_effect=[
            MagicMock(all=MagicMock(return_value=[])),
            MagicMock(all=MagicMock(return_value=[plan])),
        ]
    )
    service._exit_plan = AsyncMock(return_value=True)

    mid_session = datetime(2026, 8, 3, 11, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    stats = await service.process_live_quotes(
        {
            "JSWINFRA": SessionQuote(
                last_price=Decimal("328.50"),
                poll_high=Decimal("330.95"),
                poll_low=Decimal("325"),
            )
        },
        now=mid_session,
    )

    assert stats["targets"] == 1
    service._exit_plan.assert_awaited_once_with(
        plan, Decimal("330.54"), TradePlanStatus.TARGET_HIT
    )


@pytest.mark.quick
@pytest.mark.asyncio
async def test_process_live_quotes_target_hit_via_nse_day_high() -> None:
    """Target triggers from NSE session high when app restarts without poll history."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.providers.base import SessionQuote

    service = TradePlanService(AsyncMock())
    service.paper = AsyncMock()

    plan = MagicMock()
    plan.instrument = MagicMock()
    plan.instrument.symbol = "RELIANCE"
    plan.target_price = Decimal("2500")
    plan.stop_loss_price = Decimal("2400")
    plan.entry_limit_price = Decimal("2450")
    plan.status = TradePlanStatus.OPEN

    service.session.scalars = AsyncMock(
        side_effect=[
            MagicMock(all=MagicMock(return_value=[])),
            MagicMock(all=MagicMock(return_value=[plan])),
        ]
    )
    service._exit_plan = AsyncMock(return_value=True)

    mid_session = datetime(2026, 8, 5, 11, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    stats = await service.process_live_quotes(
        {
            "RELIANCE": SessionQuote(
                last_price=Decimal("2480"),
                nse_day_high=Decimal("2510"),
                nse_day_low=Decimal("2440"),
            )
        },
        now=mid_session,
    )

    assert stats["targets"] == 1
    service._exit_plan.assert_awaited_once_with(
        plan, Decimal("2500"), TradePlanStatus.TARGET_HIT
    )


@pytest.mark.quick
@pytest.mark.asyncio
async def test_reconcile_session_brackets_runs_nse_catchup_during_live_hours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    service = TradePlanService(AsyncMock())
    service.paper = AsyncMock()
    service.paper.match_pending_limit_orders = AsyncMock(return_value=0)
    service._sync_entries_from_orders = AsyncMock(return_value=0)
    service._close_stale_recommendation_sessions = AsyncMock(
        return_value={"cancelled_pending": 0, "square_offs": 0, "targets": 0, "stops": 0}
    )
    service.reconcile_open_plans_with_nse_day_ohlc = AsyncMock(
        return_value={"checked": 2, "targets": 1, "stops": 0, "skipped": 0, "details": []}
    )
    service._reconcile_pending_entries_with_nse_day_ohlc = AsyncMock(return_value={"entries": 1})
    service._symbols_for_active_bracket_plans = AsyncMock(return_value={"INFY"})
    service.process_live_quotes = AsyncMock(
        return_value={"entries": 0, "targets": 0, "stops": 0, "square_offs": 0}
    )
    service.paper.square_off_remaining_positions = AsyncMock(return_value=0)

    async def _fake_quotes(_session, symbols):
        assert symbols == ["INFY"]
        return {}

    monkeypatch.setattr("app.services.live_quotes.fetch_live_quotes", _fake_quotes)
    monkeypatch.setattr(
        "app.services.market_calendar.is_live_quote_session",
        lambda **kwargs: True,
    )
    monkeypatch.setattr(
        "app.services.market_calendar.is_square_off_window",
        lambda **kwargs: False,
    )
    monkeypatch.setattr(
        "app.services.market_calendar.current_session_date",
        lambda **kwargs: date(2026, 8, 5),
    )

    now = datetime(2026, 8, 5, 11, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    stats = await service.reconcile_session_brackets_after_downtime(now=now)

    assert stats["targets"] == 1
    assert stats["entries"] == 1
    service.reconcile_open_plans_with_nse_day_ohlc.assert_awaited_once()


@pytest.mark.quick
def test_eod_analysis_row_counts() -> None:
    from app.services.trade_plans import EodAnalysisReport, EodAnalysisRow

    report = EodAnalysisReport(
        recommendation_date=date(2026, 7, 30),
        as_of_date=date(2026, 7, 29),
        total_plans=2,
        pending_entry=1,
        open_positions=0,
        target_hit=1,
        stop_hit=0,
        time_exit=0,
        missed_target=0,
        closed_today=1,
        day_realized_pnl=500.0,
        cumulative_realized_pnl=500.0,
        rows=[
            EodAnalysisRow(
                symbol="INFY",
                pattern_name="Hammer",
                shares=10,
                status="Target Hit",
                entry_price=1500.0,
                exit_price=1575.0,
                target_price=1575.0,
                stop_loss_price=1450.0,
                realized_pnl=500.0,
            )
        ],
    )
    assert report.target_hit == 1
    assert report.day_realized_pnl == 500.0


@pytest.mark.quick
@pytest.mark.asyncio
async def test_include_in_session_review_excludes_later_entries() -> None:
    service = TradePlanService(AsyncMock())
    plan = MagicMock()
    plan.status = TradePlanStatus.OPEN
    plan.entry_order_id = 1
    service._entry_fill_date = AsyncMock(return_value=date(2026, 8, 3))

    included = await service._include_in_session_review(
        plan, date(2026, 7, 31), session_complete=True
    )
    assert included is False


@pytest.mark.quick
def test_display_status_expired_pending() -> None:
    service = TradePlanService(AsyncMock())
    plan = MagicMock()
    plan.status = TradePlanStatus.CANCELLED
    plan.entry_price = None
    assert service._display_status(plan, session_complete=True) == "Expired (no fill)"


@pytest.mark.quick
@pytest.mark.asyncio
async def test_process_live_quotes_square_off_at_325pm() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    service = TradePlanService(AsyncMock())
    service.paper = AsyncMock()

    plan = MagicMock()
    plan.instrument = MagicMock()
    plan.instrument.symbol = "INFY"
    plan.target_price = Decimal("1600")
    plan.stop_loss_price = Decimal("1450")
    plan.shares = 10
    plan.status = TradePlanStatus.OPEN

    service.session.scalars = AsyncMock(
        side_effect=[
            MagicMock(all=MagicMock(return_value=[])),
            MagicMock(all=MagicMock(return_value=[plan])),
        ]
    )
    service._exit_plan = AsyncMock(return_value=True)

    at_square_off = datetime(2026, 7, 30, 15, 26, tzinfo=ZoneInfo("Asia/Kolkata"))
    stats = await service.process_live_quotes(
        {"INFY": Decimal("1550")}, now=at_square_off
    )

    assert stats["square_offs"] == 1
    assert stats["targets"] == 0
    service._exit_plan.assert_awaited_once_with(
        plan, Decimal("1550"), TradePlanStatus.TIME_EXIT
    )


@pytest.mark.quick
@pytest.mark.asyncio
async def test_process_eod_skips_plans_from_future_recommendation_date() -> None:
    service = TradePlanService(AsyncMock())
    service.paper = AsyncMock()
    service.paper.match_pending_limit_orders = AsyncMock(return_value=0)
    service._sync_entries_from_orders = AsyncMock(return_value=0)
    service.paper.square_off_remaining_at_close = AsyncMock(return_value=0)

    plan = MagicMock()
    plan.instrument_id = 1
    plan.recommendation_date = date(2026, 7, 30)
    plan.stop_loss_price = Decimal("1450")
    plan.target_price = Decimal("1600")
    plan.shares = 10

    candle = MagicMock()
    candle.low = Decimal("1470")
    candle.high = Decimal("1550")
    candle.close = Decimal("1540")

    service.session.scalars = AsyncMock(
        side_effect=[
            MagicMock(all=MagicMock(return_value=[plan])),
            MagicMock(all=MagicMock(return_value=[plan])),
        ]
    )
    service._candle_for_date = AsyncMock(return_value=candle)
    service._exit_plan = AsyncMock(return_value=True)

    stats = await service.process_eod(date(2026, 7, 29))

    assert stats == {"entries_opened": 0, "targets": 0, "stops": 0, "square_offs": 0}
    service._exit_plan.assert_not_awaited()


@pytest.mark.quick
@pytest.mark.asyncio
async def test_process_eod_square_off_remaining_at_close() -> None:
    service = TradePlanService(AsyncMock())
    service.paper = AsyncMock()
    service.paper.match_pending_limit_orders = AsyncMock(return_value=0)
    service._sync_entries_from_orders = AsyncMock(return_value=0)
    service.paper.square_off_remaining_at_close = AsyncMock(return_value=0)

    plan = MagicMock()
    plan.instrument_id = 1
    plan.recommendation_date = date(2026, 7, 29)
    plan.stop_loss_price = Decimal("1450")
    plan.target_price = Decimal("1600")
    plan.shares = 10

    candle = MagicMock()
    candle.low = Decimal("1470")
    candle.high = Decimal("1550")
    candle.close = Decimal("1540")

    service.session.scalars = AsyncMock(
        side_effect=[
            MagicMock(all=MagicMock(return_value=[plan])),
            MagicMock(all=MagicMock(return_value=[plan])),
        ]
    )
    service._candle_for_date = AsyncMock(return_value=candle)
    service._exit_plan = AsyncMock(return_value=True)

    stats = await service.process_eod(date(2026, 7, 29))

    assert stats["square_offs"] == 1
    assert stats["targets"] == 0
    assert stats["stops"] == 0
    service._exit_plan.assert_awaited_once_with(
        plan, Decimal("1540"), TradePlanStatus.TIME_EXIT
    )


@pytest.mark.quick
@pytest.mark.asyncio
async def test_place_recommendation_plan_rejects_duplicate_across_session_dates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Second placement on a new recommendation_date must not create another order."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    service = TradePlanService(AsyncMock())
    service.paper = AsyncMock()
    account = MagicMock(id=1)
    instrument = MagicMock(id=10)
    service.paper.get_default_account = AsyncMock(return_value=account)
    service.paper._get_instrument = AsyncMock(return_value=instrument)
    service.paper.list_positions = AsyncMock(return_value=[])
    service.paper.place_order = AsyncMock()

    existing_plan = MagicMock()
    existing_plan.recommendation_date = date(2026, 7, 30)
    existing_plan.entry_order_id = 42
    existing_plan.status = TradePlanStatus.PENDING_ENTRY

    entry_order = MagicMock()
    entry_order.created_at = datetime(2026, 7, 31, 9, 30, tzinfo=ZoneInfo("Asia/Kolkata"))

    service.session.scalars = AsyncMock(
        return_value=MagicMock(all=MagicMock(return_value=[existing_plan]))
    )
    service.session.get = AsyncMock(return_value=entry_order)
    service.session.add = MagicMock()
    service.session.commit = AsyncMock()
    service.session.refresh = AsyncMock()

    monkeypatch.setattr(
        "app.services.market_calendar.active_market_session_date",
        lambda: date(2026, 7, 31),
    )
    monkeypatch.setattr(
        "app.services.market_calendar.current_session_date",
        lambda: date(2026, 7, 31),
    )

    with pytest.raises(ValueError, match="already exists"):
        await service.place_recommendation_plan(
            _sample_line(),
            date(2026, 7, 31),
        )

    service.paper.place_order.assert_not_awaited()


@pytest.mark.quick
@pytest.mark.asyncio
async def test_plan_applies_to_eod_bar_skips_when_entry_after_trade_date() -> None:
    service = TradePlanService(AsyncMock())
    plan = MagicMock()
    plan.recommendation_date = date(2026, 7, 29)
    service._entry_fill_date = AsyncMock(return_value=date(2026, 7, 30))

    applies = await service._plan_applies_to_eod_bar(plan, date(2026, 7, 29))

    assert applies is False


@pytest.mark.quick
@pytest.mark.asyncio
async def test_place_recommendation_plan_rejects_invalid_stop_target() -> None:
    service = TradePlanService(AsyncMock())
    service.paper = AsyncMock()
    service.paper.get_default_account = AsyncMock(return_value=MagicMock(id=1))
    service.paper._get_instrument = AsyncMock(return_value=MagicMock(id=10))
    service.paper.list_positions = AsyncMock(return_value=[])
    service.session.scalars = AsyncMock(
        return_value=MagicMock(all=MagicMock(return_value=[]))
    )

    bad_stop = _sample_line()
    bad_stop.stop_loss = 1550.0

    with pytest.raises(ValueError, match="Stop loss"):
        await service.place_recommendation_plan(bad_stop, date(2026, 7, 31))

    bad_target = _sample_line()
    bad_target.actual_sell_price = 1490.0

    with pytest.raises(ValueError, match="Target"):
        await service.place_recommendation_plan(bad_target, date(2026, 7, 31))


@pytest.mark.quick
@pytest.mark.asyncio
async def test_place_recommendation_plan_reactivates_cancelled_plan_for_same_date() -> None:
    service = TradePlanService(AsyncMock())
    service.paper = AsyncMock()
    account = MagicMock(id=1)
    instrument = MagicMock(id=174)
    service.paper.get_default_account = AsyncMock(return_value=account)
    service.paper._get_instrument = AsyncMock(return_value=instrument)
    service.paper.list_positions = AsyncMock(return_value=[])

    cancelled_plan = MagicMock()
    cancelled_plan.status = TradePlanStatus.CANCELLED
    cancelled_plan.recommendation_date = date(2026, 8, 5)

    new_order = MagicMock(id=268)
    service.paper.place_order = AsyncMock(return_value=new_order)
    service.session.scalars = AsyncMock(
        return_value=MagicMock(all=MagicMock(return_value=[]))
    )
    service.session.scalar = AsyncMock(return_value=cancelled_plan)
    service.session.commit = AsyncMock()
    service.session.refresh = AsyncMock()

    rec_date = date(2026, 8, 5)
    plan = await service.place_recommendation_plan(_sample_line(), rec_date)

    assert plan is cancelled_plan
    assert cancelled_plan.status == TradePlanStatus.PENDING_ENTRY
    assert cancelled_plan.entry_order_id == 268
    service.session.add.assert_not_called()
    service.paper.place_order.assert_awaited_once()


@pytest.mark.quick
@pytest.mark.asyncio
async def test_process_eod_skips_when_entry_filled_after_trade_date() -> None:
    service = TradePlanService(AsyncMock())
    service.paper = AsyncMock()
    service.paper.match_pending_limit_orders = AsyncMock(return_value=0)
    service._sync_entries_from_orders = AsyncMock(return_value=0)
    service.paper.square_off_remaining_at_close = AsyncMock(return_value=0)
    service._entry_fill_date = AsyncMock(return_value=date(2026, 7, 30))

    plan = MagicMock()
    plan.instrument_id = 1
    plan.recommendation_date = date(2026, 7, 29)
    plan.stop_loss_price = Decimal("1450")
    plan.target_price = Decimal("1600")
    plan.shares = 10

    candle = MagicMock()
    candle.low = Decimal("1400")
    candle.high = Decimal("1550")
    candle.close = Decimal("1540")

    service.session.scalars = AsyncMock(
        side_effect=[
            MagicMock(all=MagicMock(return_value=[plan])),
            MagicMock(all=MagicMock(return_value=[plan])),
        ]
    )
    service._candle_for_date = AsyncMock(return_value=candle)
    service._exit_plan = AsyncMock(return_value=True)

    stats = await service.process_eod(date(2026, 7, 29))

    assert stats == {"entries_opened": 0, "targets": 0, "stops": 0, "square_offs": 0}
    service._exit_plan.assert_not_awaited()


@pytest.mark.quick
@pytest.mark.asyncio
async def test_cleanup_duplicate_session_plans() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    service = TradePlanService(AsyncMock())
    service.paper = AsyncMock()
    service.paper.get_default_account = AsyncMock(return_value=MagicMock(id=1))
    service.paper._ist_date = MagicMock(return_value=date(2026, 7, 31))
    service.paper.undo_filled_buy_entry = AsyncMock()

    plan_a = MagicMock()
    plan_a.instrument = MagicMock()
    plan_a.instrument.symbol = "TCS"
    plan_a.recommendation_date = date(2026, 7, 31)
    plan_a.entry_order_id = 1
    plan_a.id = 1
    plan_a.status = TradePlanStatus.OPEN

    plan_b = MagicMock()
    plan_b.instrument = MagicMock()
    plan_b.instrument.symbol = "TCS"
    plan_b.recommendation_date = date(2026, 7, 31)
    plan_b.entry_order_id = 2
    plan_b.id = 2
    plan_b.status = TradePlanStatus.PENDING_ENTRY

    pending_order = MagicMock()
    pending_order.status = OrderStatus.PENDING
    pending_order.side = OrderSide.BUY
    pending_order.created_at = datetime(2026, 7, 31, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))

    service.session.scalars = AsyncMock(
        return_value=MagicMock(all=MagicMock(return_value=[plan_a, plan_b]))
    )

    async def _get(model, oid):
        if oid == 2:
            return pending_order
        return None

    service.session.get = AsyncMock(side_effect=_get)
    service.session.commit = AsyncMock()
    service._cleanup_rejected_exit_orders = AsyncMock(
        return_value={"reconciled_plans": 0, "cancelled_rejected_exits": 0}
    )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "app.services.market_calendar.active_market_session_date",
            lambda: date(2026, 7, 31),
        )
        mp.setattr(
            "app.services.market_calendar.current_session_date",
            lambda: date(2026, 7, 31),
        )
        stats = await service.cleanup_duplicate_session_plans()

    assert stats["cancelled_plans"] == 1
    assert stats["cancelled_orders"] == 1
    assert plan_b.status == TradePlanStatus.CANCELLED
    assert pending_order.status == OrderStatus.CANCELLED


@pytest.mark.quick
@pytest.mark.asyncio
async def test_exit_plan_reconciles_when_no_shares_held() -> None:
    service = TradePlanService(AsyncMock())
    service.paper = AsyncMock()
    service.paper.place_order = AsyncMock()

    plan = MagicMock()
    plan.instrument_id = 10
    plan.shares = 14
    plan.status = TradePlanStatus.OPEN
    plan.exit_order_id = None

    instrument = MagicMock()
    instrument.symbol = "ABCAPITAL"

    service.session.get = AsyncMock(return_value=instrument)
    service._held_quantity = AsyncMock(return_value=0)
    service._reconcile_open_plan_without_shares = AsyncMock(return_value=True)

    closed = await service._exit_plan(
        plan, Decimal("398.61"), TradePlanStatus.TARGET_HIT
    )

    assert closed is True
    service.paper.place_order.assert_not_awaited()
    service._reconcile_open_plan_without_shares.assert_awaited_once()


@pytest.mark.quick
@pytest.mark.asyncio
async def test_exit_plan_does_not_retry_after_rejected_exit() -> None:
    service = TradePlanService(AsyncMock())
    service.paper = AsyncMock()
    service.paper.place_order = AsyncMock()

    plan = MagicMock()
    plan.instrument_id = 10
    plan.shares = 14
    plan.exit_order_id = 99

    instrument = MagicMock()
    instrument.symbol = "ABCAPITAL"
    rejected = MagicMock(status=OrderStatus.REJECTED, quantity=14)

    service.session.get = AsyncMock(
        side_effect=lambda model, oid: instrument if model is Instrument else rejected
    )
    service._held_quantity = AsyncMock(return_value=14)

    closed = await service._exit_plan(
        plan, Decimal("398.61"), TradePlanStatus.TARGET_HIT
    )

    assert closed is False
    service.paper.place_order.assert_not_awaited()


@pytest.mark.quick
@pytest.mark.asyncio
async def test_cleanup_rejected_exit_orders_clears_failed_sells() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    service = TradePlanService(AsyncMock())
    service.paper = AsyncMock()
    service.paper.get_default_account = AsyncMock(return_value=MagicMock(id=1))
    service.paper._ist_date = MagicMock(return_value=date(2026, 7, 31))
    service._held_quantity = AsyncMock(return_value=0)
    service._reconcile_open_plan_without_shares = AsyncMock(return_value=True)

    open_plan = MagicMock()
    open_plan.instrument_id = 5
    open_plan.recommendation_date = date(2026, 7, 31)
    open_plan.entry_order_id = 1

    rejected_a = MagicMock()
    rejected_a.instrument_id = 5
    rejected_a.status = OrderStatus.REJECTED
    rejected_a.side = OrderSide.SELL
    rejected_a.created_at = datetime(2026, 7, 31, 14, 0, tzinfo=ZoneInfo("Asia/Kolkata"))

    rejected_b = MagicMock()
    rejected_b.instrument_id = 5
    rejected_b.status = OrderStatus.REJECTED
    rejected_b.side = OrderSide.SELL
    rejected_b.created_at = datetime(2026, 7, 31, 14, 1, tzinfo=ZoneInfo("Asia/Kolkata"))

    service.session.scalars = AsyncMock(
        side_effect=[
            MagicMock(all=MagicMock(return_value=[open_plan])),
            MagicMock(all=MagicMock(return_value=[open_plan])),
            MagicMock(all=MagicMock(return_value=[rejected_a, rejected_b])),
        ]
    )
    service.session.get = AsyncMock(return_value=MagicMock(created_at=rejected_a.created_at))
    service.session.commit = AsyncMock()

    stats = await service._cleanup_rejected_exit_orders(session_day=date(2026, 7, 31))

    assert stats["reconciled_plans"] == 1
    assert stats["cancelled_rejected_exits"] == 2
    assert rejected_a.status == OrderStatus.CANCELLED
    assert rejected_b.status == OrderStatus.CANCELLED

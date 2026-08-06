"""Bracket trade plans — limit entry, auto target/stop exits, EOD analysis."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Instrument,
    OhlcvCandle,
    OrderSide,
    OrderStatus,
    OrderType,
    PaperOrder,
    PaperPosition,
    PaperTrade,
    PaperTradePlan,
    TradePlanStatus,
)
from app.schemas import PlaceOrderRequest
from app.services.budget_allocator import AllocationLine
from app.services.market_calendar import (
    active_market_session_date,
    is_post_session_eod_ready,
    is_square_off_window,
    is_trading_day_complete,
)
from app.services.bracket_utils import is_valid_bracket_levels
from app.services.paper_trading import PaperTradingService
from app.services.app_logger import get_logger

log = get_logger(__name__)


@dataclass
class EodAnalysisRow:
    symbol: str
    pattern_name: str
    shares: int
    status: str
    entry_price: float | None
    exit_price: float | None
    target_price: float
    stop_loss_price: float
    realized_pnl: float | None


@dataclass
class EodAnalysisReport:
    recommendation_date: date
    as_of_date: date
    total_plans: int
    pending_entry: int
    open_positions: int
    target_hit: int
    stop_hit: int
    time_exit: int
    missed_target: int
    closed_today: int
    day_realized_pnl: float
    cumulative_realized_pnl: float
    rows: list[EodAnalysisRow]
    session_complete: bool = False


class TradePlanService:
    IST = ZoneInfo("Asia/Kolkata")

    def __init__(self, session: AsyncSession):
        self.session = session
        self.paper = PaperTradingService(session)

    async def _find_active_session_plan(
        self,
        account_id: int,
        instrument_id: int,
        recommendation_date: date,
    ) -> PaperTradePlan | None:
        """Active plan for symbol in this recommendation or trading session."""
        from app.services.market_calendar import active_market_session_date, current_session_date

        check_dates = {recommendation_date, active_market_session_date()}
        session_day = current_session_date()

        plans = (
            await self.session.scalars(
                select(PaperTradePlan).where(
                    PaperTradePlan.account_id == account_id,
                    PaperTradePlan.instrument_id == instrument_id,
                    PaperTradePlan.status.in_(
                        (TradePlanStatus.PENDING_ENTRY, TradePlanStatus.OPEN)
                    ),
                )
            )
        ).all()

        for plan in plans:
            if plan.recommendation_date in check_dates:
                return plan
            if plan.entry_order_id is None:
                continue
            order = await self.session.get(PaperOrder, plan.entry_order_id)
            if order is None or order.created_at is None:
                continue
            created = order.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=self.IST)
            else:
                created = created.astimezone(self.IST)
            if created.date() == session_day:
                return plan
        return None

    async def _find_plan_for_recommendation_date(
        self,
        account_id: int,
        instrument_id: int,
        recommendation_date: date,
    ) -> PaperTradePlan | None:
        return await self.session.scalar(
            select(PaperTradePlan).where(
                PaperTradePlan.account_id == account_id,
                PaperTradePlan.instrument_id == instrument_id,
                PaperTradePlan.recommendation_date == recommendation_date,
            )
        )

    async def _entry_fill_date(self, plan: PaperTradePlan) -> date | None:
        if plan.entry_order_id is None:
            return None
        order = await self.session.get(PaperOrder, plan.entry_order_id)
        if order is None or order.status != OrderStatus.FILLED or order.filled_at is None:
            return None
        filled = order.filled_at
        if filled.tzinfo is None:
            filled = filled.replace(tzinfo=self.IST)
        else:
            filled = filled.astimezone(self.IST)
        return filled.date()

    async def _order_created_date(self, plan: PaperTradePlan) -> date | None:
        if plan.entry_order_id is None:
            return None
        order = await self.session.get(PaperOrder, plan.entry_order_id)
        if order is None or order.created_at is None:
            return None
        created = order.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return created.astimezone(self.IST).date()

    async def _include_in_session_review(
        self,
        plan: PaperTradePlan,
        session_date: date,
        *,
        session_complete: bool,
    ) -> bool:
        """Past-session reviews exclude plans entered after the session trade date."""
        if not session_complete:
            return True
        entry_day = await self._entry_fill_date(plan)
        if entry_day is not None and entry_day > session_date:
            return False
        if plan.status == TradePlanStatus.PENDING_ENTRY:
            order_day = await self._order_created_date(plan)
            if order_day is not None and order_day > session_date:
                return False
        return True

    async def _cancel_pending_entry_plan(self, plan: PaperTradePlan) -> None:
        if plan.entry_order_id is not None:
            order = await self.session.get(PaperOrder, plan.entry_order_id)
            if order is not None and order.status == OrderStatus.PENDING:
                order.status = OrderStatus.CANCELLED
        plan.status = TradePlanStatus.CANCELLED
        plan.closed_at = datetime.now(timezone.utc)
        await self.session.commit()

    async def ensure_recommendation_session_closed(self, recommendation_date: date) -> dict[str, int]:
        """Cancel unfilled entries and square off OPEN plans for a finished session."""
        if not is_post_session_eod_ready(recommendation_date):
            return {"cancelled_pending": 0, "square_offs": 0, "targets": 0, "stops": 0}

        account = await self.paper.get_default_account()
        stats = {"cancelled_pending": 0, "square_offs": 0, "targets": 0, "stops": 0}

        pending = (
            await self.session.scalars(
                select(PaperTradePlan)
                .where(
                    PaperTradePlan.account_id == account.id,
                    PaperTradePlan.recommendation_date == recommendation_date,
                    PaperTradePlan.status == TradePlanStatus.PENDING_ENTRY,
                )
                .options(selectinload(PaperTradePlan.instrument))
            )
        ).all()
        for plan in pending:
            if not await self._include_in_session_review(
                plan, recommendation_date, session_complete=True
            ):
                continue
            await self._cancel_pending_entry_plan(plan)
            stats["cancelled_pending"] += 1

        eod_stats = await self.process_eod(recommendation_date)
        for key in ("square_offs", "targets", "stops"):
            stats[key] = eod_stats.get(key, 0)
        return stats

    def _display_status(self, plan: PaperTradePlan, *, session_complete: bool) -> str:
        if session_complete and plan.status == TradePlanStatus.CANCELLED and plan.entry_price is None:
            return "Expired (no fill)"
        return plan.status.value.replace("_", " ").title()

    async def _plan_applies_to_eod_bar(self, plan: PaperTradePlan, trade_date: date) -> bool:
        """Only evaluate EOD bars on/after the day the entry actually filled."""
        if plan.recommendation_date > trade_date:
            return False
        entry_day = await self._entry_fill_date(plan)
        if entry_day is not None and entry_day > trade_date:
            return False
        return True

    async def place_recommendation_plan(
        self,
        line: AllocationLine,
        recommendation_date: date,
        *,
        budget_inr: float | None = None,
        session_realized_pnl: float | None = None,
    ) -> PaperTradePlan:
        from app.services.budget_portfolio import budget_from_settings, validate_buy_against_budget

        account = await self.paper.get_default_account()
        instrument = await self.paper._get_instrument(line.symbol)

        existing = await self._find_active_session_plan(
            account.id,
            instrument.id,
            recommendation_date,
        )
        if existing:
            raise ValueError(
                f"Active trade plan already exists for {line.symbol} "
                f"(session {existing.recommendation_date})"
            )

        day_plan = await self._find_plan_for_recommendation_date(
            account.id,
            instrument.id,
            recommendation_date,
        )
        if day_plan is not None:
            if day_plan.status in (
                TradePlanStatus.TARGET_HIT,
                TradePlanStatus.STOP_HIT,
                TradePlanStatus.TIME_EXIT,
            ):
                raise ValueError(
                    f"Trade plan for {line.symbol} already closed for "
                    f"{recommendation_date.strftime('%d %b %Y')}"
                )
            if day_plan.status == TradePlanStatus.CANCELLED:
                return await self._reactivate_cancelled_plan(
                    day_plan,
                    line,
                    recommendation_date,
                    budget_inr=budget_inr,
                    session_realized_pnl=session_realized_pnl,
                )

        budget = budget_inr if budget_inr is not None else budget_from_settings()
        positions = await self.paper.list_positions()
        validate_buy_against_budget(
            budget,
            positions,
            float(line.buy_price * line.shares),
            session_realized_pnl=session_realized_pnl,
        )

        entry_limit = Decimal(str(round(line.buy_price, 4)))
        stop_loss = Decimal(str(round(line.stop_loss, 4)))
        target = Decimal(str(round(line.actual_sell_price, 4)))
        if not is_valid_bracket_levels(float(entry_limit), float(target), float(stop_loss)):
            if target <= entry_limit:
                raise ValueError(
                    f"Target ({target}) must be above entry limit ({entry_limit}) for {line.symbol}"
                )
            raise ValueError(
                f"Stop loss ({stop_loss}) must be below entry limit ({entry_limit}) for {line.symbol}"
            )

        order = await self.paper.place_order(
            PlaceOrderRequest(
                symbol=line.symbol.upper(),
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                quantity=int(line.shares),
                limit_price=entry_limit,
            ),
        )

        plan = PaperTradePlan(
            account_id=account.id,
            instrument_id=instrument.id,
            recommendation_date=recommendation_date,
            shares=int(line.shares),
            entry_limit_price=entry_limit,
            target_price=target,
            stop_loss_price=stop_loss,
            status=TradePlanStatus.PENDING_ENTRY,
            entry_order_id=order.id,
            pattern_name=line.pattern_name,
        )
        self.session.add(plan)
        await self.session.commit()
        await self.session.refresh(plan)
        return plan

    async def _reactivate_cancelled_plan(
        self,
        plan: PaperTradePlan,
        line: AllocationLine,
        recommendation_date: date,
        *,
        budget_inr: float | None = None,
        session_realized_pnl: float | None = None,
    ) -> PaperTradePlan:
        from app.services.budget_portfolio import budget_from_settings, validate_buy_against_budget

        budget = budget_inr if budget_inr is not None else budget_from_settings()
        positions = await self.paper.list_positions()
        validate_buy_against_budget(
            budget,
            positions,
            float(line.buy_price * line.shares),
            session_realized_pnl=session_realized_pnl,
        )

        entry_limit = Decimal(str(round(line.buy_price, 4)))
        stop_loss = Decimal(str(round(line.stop_loss, 4)))
        target = Decimal(str(round(line.actual_sell_price, 4)))
        if not is_valid_bracket_levels(float(entry_limit), float(target), float(stop_loss)):
            if target <= entry_limit:
                raise ValueError(
                    f"Target ({target}) must be above entry limit ({entry_limit}) for {line.symbol}"
                )
            raise ValueError(
                f"Stop loss ({stop_loss}) must be below entry limit ({entry_limit}) for {line.symbol}"
            )

        order = await self.paper.place_order(
            PlaceOrderRequest(
                symbol=line.symbol.upper(),
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                quantity=int(line.shares),
                limit_price=entry_limit,
            ),
        )

        plan.shares = int(line.shares)
        plan.entry_limit_price = entry_limit
        plan.target_price = target
        plan.stop_loss_price = stop_loss
        plan.status = TradePlanStatus.PENDING_ENTRY
        plan.entry_order_id = order.id
        plan.pattern_name = line.pattern_name
        plan.entry_price = None
        plan.exit_price = None
        plan.realized_pnl = None
        plan.exit_order_id = None
        plan.closed_at = None
        await self.session.commit()
        await self.session.refresh(plan)
        return plan

    async def _calibrate_pending_plan(
        self,
        plan: PaperTradePlan,
        line: AllocationLine,
        *,
        budget_inr: float | None = None,
        session_realized_pnl: float | None = None,
    ) -> PaperTradePlan:
        """Update a pending-entry bracket: cancel old limit, place new limit, refresh levels."""
        from app.services.budget_portfolio import budget_from_settings, validate_buy_against_budget

        if plan.status != TradePlanStatus.PENDING_ENTRY:
            raise ValueError(f"Cannot calibrate pending entry for {line.symbol} — plan is {plan.status.value}")

        budget = budget_inr if budget_inr is not None else budget_from_settings()
        positions = await self.paper.list_positions()
        validate_buy_against_budget(
            budget,
            positions,
            float(line.buy_price * line.shares),
            session_realized_pnl=session_realized_pnl,
        )

        entry_limit = Decimal(str(round(line.buy_price, 4)))
        stop_loss = Decimal(str(round(line.stop_loss, 4)))
        target = Decimal(str(round(line.actual_sell_price, 4)))
        if not is_valid_bracket_levels(float(entry_limit), float(target), float(stop_loss)):
            if target <= entry_limit:
                raise ValueError(
                    f"Target ({target}) must be above entry limit ({entry_limit}) for {line.symbol}"
                )
            raise ValueError(
                f"Stop loss ({stop_loss}) must be below entry limit ({entry_limit}) for {line.symbol}"
            )

        if plan.entry_order_id is not None:
            order = await self.session.get(PaperOrder, plan.entry_order_id)
            if order is not None and order.status == OrderStatus.PENDING:
                await self.paper.cancel_order(order.id)

        order = await self.paper.place_order(
            PlaceOrderRequest(
                symbol=line.symbol.upper(),
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                quantity=int(line.shares),
                limit_price=entry_limit,
            ),
        )

        plan.shares = int(line.shares)
        plan.entry_limit_price = entry_limit
        plan.target_price = target
        plan.stop_loss_price = stop_loss
        plan.entry_order_id = order.id
        plan.pattern_name = line.pattern_name
        await self.session.commit()
        await self.session.refresh(plan)
        return plan

    async def _calibrate_open_plan(
        self,
        plan: PaperTradePlan,
        line: AllocationLine,
    ) -> PaperTradePlan:
        """Update target/stop on an open position; entry price is unchanged."""
        if plan.status != TradePlanStatus.OPEN:
            raise ValueError(f"Cannot calibrate open plan for {line.symbol} — plan is {plan.status.value}")

        entry_ref = plan.entry_price or plan.entry_limit_price
        target = Decimal(str(round(line.actual_sell_price, 4)))
        stop_loss = Decimal(str(round(line.stop_loss, 4)))
        if not is_valid_bracket_levels(float(entry_ref), float(target), float(stop_loss)):
            raise ValueError(
                f"Invalid bracket after calibration for {line.symbol}: "
                f"stop {stop_loss} < entry {entry_ref} < target {target}"
            )

        plan.target_price = target
        plan.stop_loss_price = stop_loss
        plan.pattern_name = line.pattern_name
        await self.session.commit()
        await self.session.refresh(plan)
        return plan

    async def apply_midday_recommendation(
        self,
        line: AllocationLine,
        recommendation_date: date,
        *,
        budget_inr: float | None = None,
        session_realized_pnl: float | None = None,
    ) -> PaperTradePlan:
        """
        Place or calibrate a mid-day recommendation.

        - No active plan → new bracket (place_recommendation_plan)
        - PENDING_ENTRY → update limit buy + target/stop
        - OPEN → update target/stop only
        """
        account = await self.paper.get_default_account()
        instrument = await self.paper._get_instrument(line.symbol)

        plan = await self._find_active_session_plan(
            account.id,
            instrument.id,
            recommendation_date,
        )
        if plan is None:
            plan = await self._find_plan_for_recommendation_date(
                account.id,
                instrument.id,
                recommendation_date,
            )

        if plan is None or plan.status == TradePlanStatus.CANCELLED:
            log.info("Mid-day place new plan symbol=%s date=%s", line.symbol, recommendation_date)
            return await self.place_recommendation_plan(
                line,
                recommendation_date,
                budget_inr=budget_inr,
                session_realized_pnl=session_realized_pnl,
            )

        if plan.status in (
            TradePlanStatus.TARGET_HIT,
            TradePlanStatus.STOP_HIT,
            TradePlanStatus.TIME_EXIT,
        ):
            raise ValueError(
                f"Trade plan for {line.symbol} already closed for "
                f"{recommendation_date.strftime('%d %b %Y')}"
            )

        if plan.status == TradePlanStatus.PENDING_ENTRY:
            log.info(
                "Mid-day calibrate pending entry symbol=%s buy=%s target=%s stop=%s",
                line.symbol,
                line.buy_price,
                line.actual_sell_price,
                line.stop_loss,
            )
            return await self._calibrate_pending_plan(
                plan,
                line,
                budget_inr=budget_inr,
                session_realized_pnl=session_realized_pnl,
            )

        if plan.status == TradePlanStatus.OPEN:
            log.info(
                "Mid-day calibrate open position symbol=%s target=%s stop=%s",
                line.symbol,
                line.actual_sell_price,
                line.stop_loss,
            )
            return await self._calibrate_open_plan(plan, line)

        raise ValueError(f"Unexpected plan status for {line.symbol}: {plan.status.value}")

    async def place_all_recommendation_plans(
        self,
        lines: list[AllocationLine],
        recommendation_date: date,
        *,
        budget_inr: float | None = None,
    ) -> list[tuple[str, str, PaperTradePlan | str]]:
        results: list[tuple[str, str, PaperTradePlan | str]] = []
        for line in lines:
            if not is_valid_bracket_levels(
                line.buy_price, line.actual_sell_price, line.stop_loss
            ):
                results.append(
                    (
                        line.symbol,
                        "skipped",
                        "Invalid bracket levels (target must be above entry, stop below)",
                    )
                )
                continue
            try:
                plan = await self.place_recommendation_plan(
                    line, recommendation_date, budget_inr=budget_inr
                )
                results.append((line.symbol, "placed", plan))
            except Exception as exc:
                results.append((line.symbol, "error", str(exc)))
        return results

    async def _candle_for_date(
        self, instrument_id: int, trade_date: date
    ) -> OhlcvCandle | None:
        return await self.session.scalar(
            select(OhlcvCandle).where(
                OhlcvCandle.instrument_id == instrument_id,
                OhlcvCandle.trade_date == trade_date,
            )
        )

    async def _sync_entries_from_orders(self) -> int:
        plans = (
            await self.session.scalars(
                select(PaperTradePlan)
                .where(PaperTradePlan.status == TradePlanStatus.PENDING_ENTRY)
                .options(selectinload(PaperTradePlan.instrument))
            )
        ).all()
        opened = 0
        changed = 0
        for plan in plans:
            if plan.entry_order_id is None:
                continue
            order = await self.session.get(PaperOrder, plan.entry_order_id)
            if order is None:
                continue
            if order.status == OrderStatus.FILLED and order.filled_price is not None:
                plan.status = TradePlanStatus.OPEN
                plan.entry_price = order.filled_price
                opened += 1
                changed += 1
            elif order.status in (OrderStatus.CANCELLED, OrderStatus.REJECTED):
                plan.status = TradePlanStatus.CANCELLED
                plan.closed_at = datetime.now(timezone.utc)
                changed += 1
        if changed:
            await self.session.commit()
        return opened

    async def _fill_entry_at_price(self, plan: PaperTradePlan, fill_price: Decimal) -> bool:
        if plan.entry_order_id is None:
            return False
        order = await self.session.get(PaperOrder, plan.entry_order_id)
        if order is None or order.status != OrderStatus.PENDING:
            return False
        await self.paper._fill_order(order, fill_price=fill_price)
        if order.status == OrderStatus.FILLED:
            plan.status = TradePlanStatus.OPEN
            plan.entry_price = order.filled_price
            await self.session.commit()
            return True
        return False

    async def _held_quantity(self, plan: PaperTradePlan) -> int:
        account = await self.paper.get_default_account()
        position = await self.session.scalar(
            select(PaperPosition).where(
                PaperPosition.account_id == account.id,
                PaperPosition.instrument_id == plan.instrument_id,
            )
        )
        return position.quantity if position else 0

    async def _find_session_exit_trade(self, plan: PaperTradePlan) -> PaperTrade | None:
        from app.services.market_calendar import current_session_date

        session_day = current_session_date()
        account = await self.paper.get_default_account()
        trades = (
            await self.session.scalars(
                select(PaperTrade)
                .where(
                    PaperTrade.account_id == account.id,
                    PaperTrade.instrument_id == plan.instrument_id,
                    PaperTrade.side == OrderSide.SELL,
                )
                .order_by(PaperTrade.executed_at.desc())
            )
        ).all()
        for trade in trades:
            executed = trade.executed_at
            if executed.tzinfo is None:
                executed = executed.replace(tzinfo=timezone.utc)
            if executed.astimezone(self.IST).date() == session_day:
                return trade
        return None

    async def _reconcile_open_plan_without_shares(
        self,
        plan: PaperTradePlan,
        status: TradePlanStatus,
    ) -> bool:
        """Close an OPEN plan when holdings are already flat (e.g. prior manual exit)."""
        trade = await self._find_session_exit_trade(plan)
        if trade:
            plan.status = status
            plan.exit_order_id = trade.order_id
            plan.exit_price = trade.price
            plan.realized_pnl = trade.realized_pnl
            plan.closed_at = datetime.now(timezone.utc)
            await self.session.commit()
            return True

        plan.status = TradePlanStatus.CANCELLED
        plan.closed_at = datetime.now(timezone.utc)
        await self.session.commit()
        return False

    async def _exit_plan(
        self,
        plan: PaperTradePlan,
        exit_price: Decimal,
        status: TradePlanStatus,
    ) -> bool:
        instrument = await self.session.get(Instrument, plan.instrument_id)
        if instrument is None:
            return False

        held = await self._held_quantity(plan)
        if held <= 0:
            return await self._reconcile_open_plan_without_shares(plan, status)

        if plan.exit_order_id:
            existing = await self.session.get(PaperOrder, plan.exit_order_id)
            if existing is not None:
                if existing.status == OrderStatus.PENDING:
                    return False
                if existing.status == OrderStatus.REJECTED:
                    if existing.quantity <= held:
                        return False
                    plan.exit_order_id = None

        sell_qty = min(plan.shares, held)
        order = await self.paper.place_order(
            PlaceOrderRequest(
                symbol=instrument.symbol,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=sell_qty,
            ),
            market_fill_price=exit_price,
        )
        if order.status != OrderStatus.FILLED:
            plan.exit_order_id = order.id
            await self.session.commit()
            if order.status == OrderStatus.REJECTED and await self._held_quantity(plan) <= 0:
                return await self._reconcile_open_plan_without_shares(plan, status)
            return False

        trade = await self.session.scalar(
            select(PaperTrade).where(PaperTrade.order_id == order.id)
        )
        plan.status = status
        plan.exit_order_id = order.id
        plan.exit_price = order.filled_price
        plan.realized_pnl = trade.realized_pnl if trade else Decimal("0")
        plan.closed_at = datetime.now(timezone.utc)
        await self.session.commit()
        return True

    async def process_eod(self, trade_date: date) -> dict[str, int]:
        """Match entries and check target/stop against the EOD bar."""
        await self.paper.match_pending_limit_orders()
        await self._sync_entries_from_orders()

        stats = {"entries_opened": 0, "targets": 0, "stops": 0, "square_offs": 0}
        open_plans = (
            await self.session.scalars(
                select(PaperTradePlan).where(PaperTradePlan.status == TradePlanStatus.OPEN)
            )
        ).all()

        for plan in open_plans:
            if not await self._plan_applies_to_eod_bar(plan, trade_date):
                continue
            candle = await self._candle_for_date(plan.instrument_id, trade_date)
            if candle is None:
                continue

            low = Decimal(str(candle.low))
            high = Decimal(str(candle.high))

            if low <= plan.stop_loss_price:
                if await self._exit_plan(plan, plan.stop_loss_price, TradePlanStatus.STOP_HIT):
                    stats["stops"] += 1
            elif high >= plan.target_price:
                if await self._exit_plan(plan, plan.target_price, TradePlanStatus.TARGET_HIT):
                    stats["targets"] += 1

        # Fallback: any position still open at EOD is squared off at the close.
        remaining = (
            await self.session.scalars(
                select(PaperTradePlan).where(PaperTradePlan.status == TradePlanStatus.OPEN)
            )
        ).all()
        for plan in remaining:
            if not await self._plan_applies_to_eod_bar(plan, trade_date):
                continue
            candle = await self._candle_for_date(plan.instrument_id, trade_date)
            if candle is None:
                continue
            close = Decimal(str(candle.close))
            if await self._exit_plan(plan, close, TradePlanStatus.TIME_EXIT):
                stats["square_offs"] += 1

        remaining_closed = await self.paper.square_off_remaining_at_close(trade_date)
        stats["square_offs"] += remaining_closed

        return stats

    async def process_live_quotes(
        self,
        quotes: dict[str, "SessionQuote | Decimal"],
        *,
        now: datetime | None = None,
    ) -> dict[str, int]:
        """Intraday entry/exit checks using LTP samples accumulated across live polls."""
        from app.providers.base import SessionQuote

        normalized: dict[str, SessionQuote] = {}
        for symbol, quote in quotes.items():
            if isinstance(quote, Decimal):
                normalized[symbol] = SessionQuote(last_price=quote)
            else:
                normalized[symbol] = quote

        stats = {"entries": 0, "targets": 0, "stops": 0, "square_offs": 0}
        force_square_off = is_square_off_window(now=now)

        pending = (
            await self.session.scalars(
                select(PaperTradePlan)
                .where(PaperTradePlan.status == TradePlanStatus.PENDING_ENTRY)
                .options(selectinload(PaperTradePlan.instrument))
            )
        ).all()
        for plan in pending:
            symbol = plan.instrument.symbol
            quote = normalized.get(symbol)
            if quote is None:
                continue
            if quote.observed_low <= plan.entry_limit_price:
                if await self._fill_entry_at_price(plan, plan.entry_limit_price):
                    stats["entries"] += 1

        open_plans = (
            await self.session.scalars(
                select(PaperTradePlan)
                .where(PaperTradePlan.status == TradePlanStatus.OPEN)
                .options(selectinload(PaperTradePlan.instrument))
            )
        ).all()
        for plan in open_plans:
            symbol = plan.instrument.symbol
            quote = normalized.get(symbol)
            if quote is None:
                continue
            ltp = quote.last_price
            if force_square_off:
                if await self._exit_plan(plan, ltp, TradePlanStatus.TIME_EXIT):
                    stats["square_offs"] += 1
                continue
            if (
                quote.observed_low <= plan.stop_loss_price
                and plan.stop_loss_price < plan.entry_limit_price
            ):
                if await self._exit_plan(plan, plan.stop_loss_price, TradePlanStatus.STOP_HIT):
                    stats["stops"] += 1
            elif (
                quote.observed_high >= plan.target_price
                and plan.target_price > plan.entry_limit_price
            ):
                if await self._exit_plan(plan, plan.target_price, TradePlanStatus.TARGET_HIT):
                    stats["targets"] += 1

        return stats

    async def reconcile_open_plans_with_nse_day_ohlc(self) -> dict[str, object]:
        """One-off catch-up: check session high/low vs target/stop for OPEN bracket plans."""
        import asyncio
        import time

        from app.services.intraday_chart import fetch_session_ohlc_sync

        stats: dict[str, object] = {
            "checked": 0,
            "targets": 0,
            "stops": 0,
            "skipped": 0,
            "details": [],
        }
        details: list[dict[str, object]] = []

        open_plans = (
            await self.session.scalars(
                select(PaperTradePlan)
                .where(PaperTradePlan.status == TradePlanStatus.OPEN)
                .options(selectinload(PaperTradePlan.instrument))
            )
        ).all()

        for plan in open_plans:
            instrument = plan.instrument
            if instrument is None:
                continue
            symbol = instrument.symbol
            quote = await asyncio.to_thread(fetch_session_ohlc_sync, symbol)
            time.sleep(0.3)
            day_high = quote.get("high")
            day_low = quote.get("low")
            stats["checked"] = int(stats["checked"]) + 1

            detail: dict[str, object] = {
                "symbol": symbol,
                "target": float(plan.target_price),
                "stop": float(plan.stop_loss_price),
                "entry_limit": float(plan.entry_limit_price),
                "day_high": day_high,
                "day_low": day_low,
                "last": quote.get("last"),
                "source": quote.get("source"),
                "action": "none",
            }

            if day_high is None or day_low is None:
                detail["action"] = "no_quote"
                stats["skipped"] = int(stats["skipped"]) + 1
                details.append(detail)
                continue

            high = Decimal(str(day_high))
            low = Decimal(str(day_low))

            if (
                low <= plan.stop_loss_price
                and plan.stop_loss_price < plan.entry_limit_price
            ):
                if await self._exit_plan(plan, plan.stop_loss_price, TradePlanStatus.STOP_HIT):
                    stats["stops"] = int(stats["stops"]) + 1
                    detail["action"] = "stop_hit"
            elif (
                high >= plan.target_price
                and plan.target_price > plan.entry_limit_price
            ):
                if await self._exit_plan(plan, plan.target_price, TradePlanStatus.TARGET_HIT):
                    stats["targets"] = int(stats["targets"]) + 1
                    detail["action"] = "target_hit"

            details.append(detail)

        stats["details"] = details
        return stats

    async def _reconcile_pending_entries_with_nse_day_ohlc(self) -> dict[str, int]:
        """Fill limit entries missed while the UI was offline (session low vs entry limit)."""
        import asyncio
        import time

        from app.services.intraday_chart import fetch_session_ohlc_sync

        stats = {"entries": 0}
        pending = (
            await self.session.scalars(
                select(PaperTradePlan)
                .where(PaperTradePlan.status == TradePlanStatus.PENDING_ENTRY)
                .options(selectinload(PaperTradePlan.instrument))
            )
        ).all()

        for plan in pending:
            instrument = plan.instrument
            if instrument is None:
                continue
            quote = await asyncio.to_thread(fetch_session_ohlc_sync, instrument.symbol)
            time.sleep(0.3)
            day_low = quote.get("low")
            if day_low is None:
                continue
            if Decimal(str(day_low)) <= plan.entry_limit_price:
                if await self._fill_entry_at_price(plan, plan.entry_limit_price):
                    stats["entries"] += 1
        return stats

    async def _close_stale_recommendation_sessions(self, *, now: datetime | None = None) -> dict[str, int]:
        """Run EOD close for any recommendation dates whose session has finished."""
        from app.services.market_calendar import is_post_session_eod_ready

        account = await self.paper.get_default_account()
        plans = (
            await self.session.scalars(
                select(PaperTradePlan)
                .where(
                    PaperTradePlan.account_id == account.id,
                    PaperTradePlan.status.in_(
                        (TradePlanStatus.PENDING_ENTRY, TradePlanStatus.OPEN)
                    ),
                )
            )
        ).all()
        rec_dates = sorted({plan.recommendation_date for plan in plans})
        totals = {
            "cancelled_pending": 0,
            "square_offs": 0,
            "targets": 0,
            "stops": 0,
        }
        for rec_date in rec_dates:
            if not is_post_session_eod_ready(rec_date, now=now):
                continue
            closed = await self.ensure_recommendation_session_closed(rec_date)
            for key in totals:
                totals[key] += int(closed.get(key, 0))
        return totals

    async def _symbols_for_active_bracket_plans(self) -> set[str]:
        symbols: set[str] = set()
        plans = (
            await self.session.scalars(
                select(PaperTradePlan)
                .where(
                    PaperTradePlan.status.in_(
                        (TradePlanStatus.PENDING_ENTRY, TradePlanStatus.OPEN)
                    )
                )
                .options(selectinload(PaperTradePlan.instrument))
            )
        ).all()
        for plan in plans:
            if plan.instrument is not None:
                symbols.add(plan.instrument.symbol)
        return symbols

    async def reconcile_session_brackets_after_downtime(
        self,
        *,
        now: datetime | None = None,
    ) -> dict[str, object]:
        """Catch up bracket fills/exits missed while Streamlit was offline."""
        from app.services.live_quotes import fetch_live_quotes
        from app.services.market_calendar import (
            current_session_date,
            is_live_quote_session,
            is_square_off_window,
        )

        session_day = current_session_date(now=now)
        stats: dict[str, object] = {
            "session_date": session_day.isoformat(),
            "entries": 0,
            "targets": 0,
            "stops": 0,
            "square_offs": 0,
            "cancelled_pending": 0,
        }

        await self.paper.match_pending_limit_orders()
        await self._sync_entries_from_orders()

        stale = await self._close_stale_recommendation_sessions(now=now)
        for key in ("cancelled_pending", "square_offs", "targets", "stops"):
            stats[key] = int(stats.get(key, 0)) + stale.get(key, 0)

        if is_live_quote_session(now=now):
            nse_stats = await self.reconcile_open_plans_with_nse_day_ohlc()
            stats["targets"] = int(stats["targets"]) + int(nse_stats.get("targets", 0))
            stats["stops"] = int(stats["stops"]) + int(nse_stats.get("stops", 0))
            stats["nse_plans_checked"] = int(nse_stats.get("checked", 0))

            pending_stats = await self._reconcile_pending_entries_with_nse_day_ohlc()
            stats["entries"] = int(stats["entries"]) + pending_stats.get("entries", 0)

            symbols = await self._symbols_for_active_bracket_plans()
            if symbols:
                quotes = await fetch_live_quotes(self.session, sorted(symbols))
                live_stats = await self.process_live_quotes(quotes, now=now)
                for key in ("entries", "targets", "stops", "square_offs"):
                    stats[key] = int(stats.get(key, 0)) + int(live_stats.get(key, 0))
                if is_square_off_window(now=now):
                    ltp_map = {sym: q.last_price for sym, q in quotes.items()}
                    remaining = await self.paper.square_off_remaining_positions(
                        ltp_map,
                        now=now,
                    )
                    stats["square_offs"] = int(stats["square_offs"]) + remaining

        return stats

    async def build_eod_analysis(
        self,
        recommendation_date: date,
        *,
        as_of_date: date | None = None,
    ) -> EodAnalysisReport:
        trade_date = as_of_date or recommendation_date
        active = active_market_session_date()
        session_complete = recommendation_date < active or is_trading_day_complete(
            recommendation_date
        )
        if session_complete:
            await self.ensure_recommendation_session_closed(recommendation_date)

        account = await self.paper.get_default_account()

        plans = (
            await self.session.scalars(
                select(PaperTradePlan)
                .where(
                    PaperTradePlan.account_id == account.id,
                    PaperTradePlan.recommendation_date == recommendation_date,
                )
                .options(selectinload(PaperTradePlan.instrument))
                .order_by(PaperTradePlan.id)
            )
        ).all()

        cumulative = Decimal("0")
        rows: list[EodAnalysisRow] = []

        pending = open_count = target = stop = time_exit = missed_target = 0
        closed_today = 0
        reviewed_plans = 0

        for plan in plans:
            if not await self._include_in_session_review(
                plan, recommendation_date, session_complete=session_complete
            ):
                continue

            reviewed_plans += 1
            status = plan.status.value
            if plan.status == TradePlanStatus.PENDING_ENTRY:
                pending += 1
            elif plan.status == TradePlanStatus.OPEN:
                open_count += 1
            elif plan.status == TradePlanStatus.TARGET_HIT:
                target += 1
            elif plan.status == TradePlanStatus.STOP_HIT:
                stop += 1
            elif plan.status == TradePlanStatus.TIME_EXIT:
                time_exit += 1

            entry_made = plan.status in (
                TradePlanStatus.OPEN,
                TradePlanStatus.TARGET_HIT,
                TradePlanStatus.STOP_HIT,
                TradePlanStatus.TIME_EXIT,
            ) or plan.entry_price is not None
            if entry_made and plan.status != TradePlanStatus.TARGET_HIT:
                ref = plan.exit_price
                if ref is not None and ref < plan.target_price:
                    missed_target += 1

            if plan.realized_pnl is not None:
                cumulative += plan.realized_pnl

            if plan.closed_at and self._closed_on_ist(plan.closed_at, trade_date):
                closed_today += 1

            rows.append(
                EodAnalysisRow(
                    symbol=plan.instrument.symbol,
                    pattern_name=plan.pattern_name or "—",
                    shares=plan.shares,
                    status=self._display_status(plan, session_complete=session_complete),
                    entry_price=float(plan.entry_price) if plan.entry_price else None,
                    exit_price=float(plan.exit_price) if plan.exit_price else None,
                    target_price=float(plan.target_price),
                    stop_loss_price=float(plan.stop_loss_price),
                    realized_pnl=float(plan.realized_pnl) if plan.realized_pnl is not None else None,
                )
            )

        day_pnl = await self.paper.day_realized_pnl_from_trades(trade_date)

        if session_complete:
            pending = 0
            open_count = 0

        return EodAnalysisReport(
            recommendation_date=recommendation_date,
            as_of_date=trade_date,
            total_plans=reviewed_plans,
            pending_entry=pending,
            open_positions=open_count,
            target_hit=target,
            stop_hit=stop,
            time_exit=time_exit,
            missed_target=missed_target,
            closed_today=closed_today,
            day_realized_pnl=float(day_pnl),
            cumulative_realized_pnl=float(cumulative),
            rows=rows,
            session_complete=session_complete,
        )

    def _closed_on_ist(self, closed_at: datetime, trade_date: date) -> bool:
        if closed_at.tzinfo is None:
            closed_at = closed_at.replace(tzinfo=timezone.utc)
        return closed_at.astimezone(self.IST).date() == trade_date

    async def list_plans_for_date(self, recommendation_date: date) -> list[PaperTradePlan]:
        account = await self.paper.get_default_account()
        return list(
            (
                await self.session.scalars(
                    select(PaperTradePlan)
                    .where(
                        PaperTradePlan.account_id == account.id,
                        PaperTradePlan.recommendation_date == recommendation_date,
                    )
                    .options(selectinload(PaperTradePlan.instrument))
                )
            ).all()
        )

    async def day_realized_pnl(self, trade_date: date) -> Decimal:
        return await self.paper.day_realized_pnl_from_trades(trade_date)

    async def cleanup_duplicate_session_plans(
        self,
        *,
        session_date: date | None = None,
    ) -> dict[str, int]:
        """Remove duplicate bracket plans/orders from accidental double placement."""
        from app.services.market_calendar import active_market_session_date, current_session_date

        session_day = session_date or current_session_date()
        check_dates = {session_day, active_market_session_date()}

        account = await self.paper.get_default_account()
        plans = list(
            (
                await self.session.scalars(
                    select(PaperTradePlan)
                    .where(
                        PaperTradePlan.account_id == account.id,
                        PaperTradePlan.status.in_(
                            (TradePlanStatus.PENDING_ENTRY, TradePlanStatus.OPEN)
                        ),
                    )
                    .options(selectinload(PaperTradePlan.instrument))
                )
            ).all()
        )

        session_plans: list[PaperTradePlan] = []
        for plan in plans:
            if plan.instrument is None:
                continue
            if plan.recommendation_date in check_dates:
                session_plans.append(plan)
                continue
            if plan.entry_order_id is None:
                continue
            order = await self.session.get(PaperOrder, plan.entry_order_id)
            if order is not None and self.paper._ist_date(order.created_at) == session_day:
                session_plans.append(plan)

        by_symbol: dict[str, list[PaperTradePlan]] = {}
        for plan in session_plans:
            by_symbol.setdefault(plan.instrument.symbol, []).append(plan)

        cancelled_orders = 0
        cancelled_plans = 0
        undone_fills = 0
        changed = False

        for group in by_symbol.values():
            if len(group) <= 1:
                continue
            group.sort(key=lambda p: p.entry_order_id or p.id)
            for dup in group[1:]:
                dup.status = TradePlanStatus.CANCELLED
                cancelled_plans += 1
                changed = True
                if dup.entry_order_id is None:
                    continue
                order = await self.session.get(PaperOrder, dup.entry_order_id)
                if order is None:
                    continue
                if order.status == OrderStatus.PENDING:
                    order.status = OrderStatus.CANCELLED
                    cancelled_orders += 1
                elif order.status == OrderStatus.FILLED and order.side == OrderSide.BUY:
                    await self.paper.undo_filled_buy_entry(order)
                    undone_fills += 1

        if changed:
            await self.session.commit()

        exit_stats = await self._cleanup_rejected_exit_orders(session_day=session_day)

        return {
            "cancelled_plans": cancelled_plans,
            "cancelled_orders": cancelled_orders,
            "undone_fills": undone_fills,
            **exit_stats,
        }

    async def _cleanup_rejected_exit_orders(
        self,
        *,
        session_day: date,
    ) -> dict[str, int]:
        """Reconcile stuck OPEN plans and hide duplicate failed bracket SELL attempts."""
        from app.services.market_calendar import active_market_session_date

        account = await self.paper.get_default_account()
        check_dates = {session_day, active_market_session_date()}

        open_plans = (
            await self.session.scalars(
                select(PaperTradePlan)
                .where(
                    PaperTradePlan.account_id == account.id,
                    PaperTradePlan.status == TradePlanStatus.OPEN,
                )
                .options(selectinload(PaperTradePlan.instrument))
            )
        ).all()

        reconciled_plans = 0
        for plan in open_plans:
            if await self._held_quantity(plan) > 0:
                continue
            if await self._reconcile_open_plan_without_shares(
                plan, TradePlanStatus.TIME_EXIT
            ):
                reconciled_plans += 1

        all_plans = (
            await self.session.scalars(
                select(PaperTradePlan)
                .where(PaperTradePlan.account_id == account.id)
                .options(selectinload(PaperTradePlan.instrument))
            )
        ).all()
        bracket_instruments: set[int] = set()
        for plan in all_plans:
            if plan.instrument_id is None:
                continue
            if plan.recommendation_date in check_dates:
                bracket_instruments.add(plan.instrument_id)
                continue
            if plan.entry_order_id is None:
                continue
            order = await self.session.get(PaperOrder, plan.entry_order_id)
            if order is not None and self.paper._ist_date(order.created_at) == session_day:
                bracket_instruments.add(plan.instrument_id)

        orders = (
            await self.session.scalars(
                select(PaperOrder).where(
                    PaperOrder.account_id == account.id,
                    PaperOrder.side == OrderSide.SELL,
                    PaperOrder.status == OrderStatus.REJECTED,
                )
            )
        ).all()

        cancelled_rejected = 0
        changed = False
        for order in orders:
            if order.instrument_id not in bracket_instruments:
                continue
            if order.created_at is None or self.paper._ist_date(order.created_at) != session_day:
                continue
            order.status = OrderStatus.CANCELLED
            cancelled_rejected += 1
            changed = True

        if changed:
            await self.session.commit()

        return {
            "reconciled_plans": reconciled_plans,
            "cancelled_rejected_exits": cancelled_rejected,
        }

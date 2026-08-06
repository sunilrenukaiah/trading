from datetime import date, datetime, timezone, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Instrument,
    OhlcvCandle,
    OrderSide,
    OrderStatus,
    OrderType,
    PaperAccount,
    PaperOrder,
    PaperPosition,
    PaperTrade,
    PaperTradePlan,
    TradePlanStatus,
)
from app.schemas import (
    AccountOut,
    OrderOut,
    PlaceOrderRequest,
    PositionOut,
    PositionSource,
    TradeOut,
)
from app.services.market_calendar import IST, is_square_off_window


class PaperTradingService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_default_account(self) -> PaperAccount:
        account = await self.session.scalar(select(PaperAccount).limit(1))
        if not account:
            raise ValueError("No paper account found")
        return account

    async def _get_instrument(self, symbol: str) -> Instrument:
        instrument = await self.session.scalar(
            select(Instrument).where(Instrument.symbol == symbol.upper())
        )
        if not instrument:
            raise ValueError(f"Instrument {symbol} not found")
        return instrument

    async def _latest_close(self, instrument_id: int) -> Decimal | None:
        return await self.session.scalar(
            select(OhlcvCandle.close)
            .where(OhlcvCandle.instrument_id == instrument_id)
            .order_by(OhlcvCandle.trade_date.desc())
            .limit(1)
        )

    async def place_order(
        self,
        request: PlaceOrderRequest,
        *,
        market_fill_price: Decimal | None = None,
    ) -> PaperOrder:
        account = await self.get_default_account()
        instrument = await self._get_instrument(request.symbol)

        if request.order_type == OrderType.LIMIT and request.limit_price is None:
            raise ValueError("Limit price required for LIMIT orders")

        order = PaperOrder(
            account_id=account.id,
            instrument_id=instrument.id,
            side=request.side,
            order_type=request.order_type,
            quantity=request.quantity,
            limit_price=request.limit_price,
            status=OrderStatus.PENDING,
        )
        self.session.add(order)
        await self.session.flush()

        if order.order_type == OrderType.MARKET:
            await self._fill_order(order, fill_price=market_fill_price)
        await self.session.commit()
        await self.session.refresh(order)
        return order

    async def cancel_order(self, order_id: int) -> PaperOrder:
        account = await self.get_default_account()
        order = await self.session.scalar(
            select(PaperOrder)
            .where(PaperOrder.id == order_id, PaperOrder.account_id == account.id)
            .options(selectinload(PaperOrder.instrument))
        )
        if not order:
            raise ValueError("Order not found")
        if order.status != OrderStatus.PENDING:
            raise ValueError("Only pending orders can be cancelled")
        order.status = OrderStatus.CANCELLED
        await self.session.commit()
        await self.session.refresh(order)
        return order

    async def undo_filled_buy_entry(self, order: PaperOrder) -> None:
        """Reverse a filled BUY entry (used when removing duplicate bracket orders)."""
        if order.status != OrderStatus.FILLED or order.side != OrderSide.BUY:
            raise ValueError("Only filled BUY entries can be undone")
        if order.filled_price is None:
            raise ValueError("Filled order missing price")

        account = await self.session.get(PaperAccount, order.account_id)
        if account is None:
            raise ValueError("Account not found")

        position = await self.session.scalar(
            select(PaperPosition).where(
                PaperPosition.account_id == order.account_id,
                PaperPosition.instrument_id == order.instrument_id,
            )
        )
        if position is None or position.quantity < order.quantity:
            raise ValueError("Insufficient position to undo fill")

        refund = order.filled_price * order.quantity
        account.cash_balance += refund

        remaining_qty = position.quantity - order.quantity
        if remaining_qty == 0:
            await self.session.delete(position)
        else:
            total_cost = position.avg_cost * position.quantity - refund
            position.quantity = remaining_qty
            position.avg_cost = total_cost / remaining_qty

        trade = await self.session.scalar(
            select(PaperTrade).where(PaperTrade.order_id == order.id)
        )
        if trade:
            await self.session.delete(trade)

        order.status = OrderStatus.CANCELLED
        order.filled_price = None
        order.filled_at = None

    async def _fill_order(self, order: PaperOrder, fill_price: Decimal | None = None) -> None:
        account = await self.session.get(PaperAccount, order.account_id)
        instrument = await self.session.get(Instrument, order.instrument_id)
        if not account or not instrument:
            raise ValueError("Invalid order context")

        price = fill_price or await self._latest_close(instrument.id)
        if price is None:
            order.status = OrderStatus.REJECTED
            return

        cost = price * order.quantity
        realized_pnl = Decimal("0")

        position = await self.session.scalar(
            select(PaperPosition).where(
                PaperPosition.account_id == account.id,
                PaperPosition.instrument_id == instrument.id,
            )
        )

        if order.side == OrderSide.BUY:
            if account.cash_balance < cost:
                order.status = OrderStatus.REJECTED
                return
            account.cash_balance -= cost
            if position:
                total_qty = position.quantity + order.quantity
                position.avg_cost = (
                    (position.avg_cost * position.quantity) + cost
                ) / total_qty
                position.quantity = total_qty
            else:
                self.session.add(
                    PaperPosition(
                        account_id=account.id,
                        instrument_id=instrument.id,
                        quantity=order.quantity,
                        avg_cost=price,
                    )
                )
        else:
            if not position or position.quantity < order.quantity:
                order.status = OrderStatus.REJECTED
                return
            proceeds = cost
            account.cash_balance += proceeds
            realized_pnl = (price - position.avg_cost) * order.quantity
            position.quantity -= order.quantity
            if position.quantity == 0:
                await self.session.delete(position)

        order.status = OrderStatus.FILLED
        order.filled_price = price
        order.filled_at = datetime.now(timezone.utc)

        self.session.add(
            PaperTrade(
                account_id=account.id,
                order_id=order.id,
                instrument_id=instrument.id,
                side=order.side,
                quantity=order.quantity,
                price=price,
                realized_pnl=realized_pnl,
            )
        )

    async def match_pending_limit_orders(self) -> int:
        pending = (
            await self.session.scalars(
                select(PaperOrder).where(
                    PaperOrder.status == OrderStatus.PENDING,
                    PaperOrder.order_type == OrderType.LIMIT,
                )
            )
        ).all()

        filled = 0
        for order in pending:
            if order.limit_price is None:
                continue
            latest_close = await self._latest_close(order.instrument_id)
            if latest_close is None:
                continue

            should_fill = False
            if order.side == OrderSide.BUY and latest_close <= order.limit_price:
                should_fill = True
            elif order.side == OrderSide.SELL and latest_close >= order.limit_price:
                should_fill = True

            if should_fill:
                await self._fill_order(order, fill_price=order.limit_price)
                filled += 1

        if filled:
            await self.session.commit()
        return filled

    async def get_account_summary(self) -> AccountOut:
        account = await self.get_default_account()
        positions = (
            await self.session.scalars(
                select(PaperPosition)
                .where(PaperPosition.account_id == account.id, PaperPosition.quantity > 0)
                .options(selectinload(PaperPosition.instrument))
            )
        ).all()

        equity_value = Decimal("0")
        unrealized_pnl = Decimal("0")
        for pos in positions:
            mark = await self._latest_close(pos.instrument_id)
            if mark is None:
                continue
            market_value = mark * pos.quantity
            equity_value += market_value
            unrealized_pnl += (mark - pos.avg_cost) * pos.quantity

        total_value = account.cash_balance + equity_value
        realized_pnl = total_value - account.initial_cash - unrealized_pnl

        return AccountOut(
            name=account.name,
            cash_balance=account.cash_balance,
            equity_value=equity_value,
            total_value=total_value,
            unrealized_pnl=unrealized_pnl,
            realized_pnl=realized_pnl,
            initial_cash=account.initial_cash,
        )

    async def realized_pnl_after_tax_summary(self):
        from app.services.trade_tax import (
            DualBrokerRealizedPnlSummary,
            summarize_sell_trades_dual_broker,
        )

        account = await self.get_default_account()
        trades = (
            await self.session.scalars(
                select(PaperTrade).where(
                    PaperTrade.account_id == account.id,
                    PaperTrade.side == OrderSide.SELL,
                )
            )
        ).all()
        sells: list[tuple[int, float, float]] = []
        for trade in trades:
            qty = int(trade.quantity)
            if qty <= 0:
                continue
            sell_price = float(trade.price)
            gross = float(trade.realized_pnl)
            buy_price = sell_price - gross / qty
            sells.append((qty, buy_price, sell_price))
        if not sells:
            return DualBrokerRealizedPnlSummary.empty()
        return summarize_sell_trades_dual_broker(sells)

    async def recommendation_position_symbols(self) -> set[str]:
        """Symbols with an active recommendation bracket plan (Place trade / Place all)."""
        account = await self.get_default_account()
        plans = (
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
        return {
            plan.instrument.symbol
            for plan in plans
            if plan.instrument is not None
        }

    async def symbols_with_open_bracket_plans(self) -> set[str]:
        """Symbols still managed by an OPEN bracket plan (exit via bracket only)."""
        account = await self.get_default_account()
        plans = (
            await self.session.scalars(
                select(PaperTradePlan)
                .where(
                    PaperTradePlan.account_id == account.id,
                    PaperTradePlan.status == TradePlanStatus.OPEN,
                )
                .options(selectinload(PaperTradePlan.instrument))
            )
        ).all()
        return {
            plan.instrument.symbol
            for plan in plans
            if plan.instrument is not None
        }

    async def list_positions(self) -> list[PositionOut]:
        account = await self.get_default_account()
        positions = (
            await self.session.scalars(
                select(PaperPosition)
                .where(PaperPosition.account_id == account.id, PaperPosition.quantity > 0)
                .options(selectinload(PaperPosition.instrument))
            )
        ).all()
        rec_symbols = await self.recommendation_position_symbols()

        result: list[PositionOut] = []
        for pos in positions:
            mark = await self._latest_close(pos.instrument_id)
            market_value = mark * pos.quantity if mark else None
            unrealized = (mark - pos.avg_cost) * pos.quantity if mark else None
            symbol = pos.instrument.symbol
            result.append(
                PositionOut(
                    symbol=symbol,
                    name=pos.instrument.name,
                    quantity=pos.quantity,
                    avg_cost=pos.avg_cost,
                    mark_price=mark,
                    market_value=market_value,
                    unrealized_pnl=unrealized,
                    source=(
                        PositionSource.RECOMMENDATION
                        if symbol in rec_symbols
                        else PositionSource.MANUAL
                    ),
                )
            )
        return result

    async def day_realized_pnl_from_trades(self, trade_date: date) -> Decimal:
        """Sum realized P&L from all paper trades closed on trade_date (IST)."""
        account = await self.get_default_account()
        trades = (
            await self.session.scalars(
                select(PaperTrade).where(PaperTrade.account_id == account.id)
            )
        ).all()
        total = Decimal("0")
        for trade in trades:
            if trade.executed_at is None:
                continue
            executed = trade.executed_at
            if executed.tzinfo is None:
                executed = executed.replace(tzinfo=timezone.utc)
            if executed.astimezone(IST).date() == trade_date:
                total += trade.realized_pnl
        return total

    async def square_off_remaining_positions(
        self,
        quotes: dict[str, Decimal],
        *,
        now=None,
    ) -> int:
        """Market-sell any open paper holdings still open after bracket square-off (3:25 PM IST)."""
        if not is_square_off_window(now=now):
            return 0

        account = await self.get_default_account()
        bracket_symbols = await self.symbols_with_open_bracket_plans()
        positions = (
            await self.session.scalars(
                select(PaperPosition)
                .where(PaperPosition.account_id == account.id, PaperPosition.quantity > 0)
                .options(selectinload(PaperPosition.instrument))
            )
        ).all()

        closed = 0
        for pos in positions:
            symbol = pos.instrument.symbol
            if symbol in bracket_symbols:
                continue
            ltp = quotes.get(symbol)
            if ltp is None:
                continue
            order = await self.place_order(
                PlaceOrderRequest(
                    symbol=symbol,
                    side=OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    quantity=pos.quantity,
                ),
                market_fill_price=ltp,
            )
            if order.status == OrderStatus.FILLED:
                closed += 1
        return closed

    async def square_off_remaining_at_close(self, trade_date: date) -> int:
        """EOD fallback: sell manual holdings still open using the session close."""
        account = await self.get_default_account()
        bracket_symbols = await self.symbols_with_open_bracket_plans()
        positions = (
            await self.session.scalars(
                select(PaperPosition)
                .where(PaperPosition.account_id == account.id, PaperPosition.quantity > 0)
                .options(selectinload(PaperPosition.instrument))
            )
        ).all()

        closed = 0
        for pos in positions:
            symbol = pos.instrument.symbol
            if symbol in bracket_symbols:
                continue
            candle = await self.session.scalar(
                select(OhlcvCandle)
                .where(
                    OhlcvCandle.instrument_id == pos.instrument_id,
                    OhlcvCandle.trade_date == trade_date,
                )
                .limit(1)
            )
            if candle is None:
                continue
            close = Decimal(str(candle.close))
            order = await self.place_order(
                PlaceOrderRequest(
                    symbol=pos.instrument.symbol,
                    side=OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    quantity=pos.quantity,
                ),
                market_fill_price=close,
            )
            if order.status == OrderStatus.FILLED:
                closed += 1
        return closed

    def _ist_date(self, dt: datetime) -> date:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(IST).date()

    async def list_orders(self, *, session_date: date | None = None) -> list[OrderOut]:
        account = await self.get_default_account()
        orders = (
            await self.session.scalars(
                select(PaperOrder)
                .where(PaperOrder.account_id == account.id)
                .options(selectinload(PaperOrder.instrument))
                .order_by(PaperOrder.created_at.desc())
            )
        ).all()
        if session_date is not None:
            orders = [o for o in orders if self._ist_date(o.created_at) == session_date]
        return [
            OrderOut(
                id=o.id,
                symbol=o.instrument.symbol,
                side=o.side,
                order_type=o.order_type,
                quantity=o.quantity,
                limit_price=o.limit_price,
                status=o.status,
                filled_price=o.filled_price,
                filled_at=o.filled_at,
                created_at=o.created_at,
            )
            for o in orders
        ]

    async def list_trades(self, *, session_date: date | None = None) -> list[TradeOut]:
        account = await self.get_default_account()
        trades = (
            await self.session.scalars(
                select(PaperTrade)
                .where(PaperTrade.account_id == account.id)
                .order_by(PaperTrade.executed_at.desc())
            )
        ).all()
        if session_date is not None:
            trades = [t for t in trades if self._ist_date(t.executed_at) == session_date]

        result: list[TradeOut] = []
        for trade in trades:
            instrument = await self.session.get(Instrument, trade.instrument_id)
            result.append(
                TradeOut(
                    id=trade.id,
                    symbol=instrument.symbol if instrument else "UNKNOWN",
                    side=trade.side,
                    quantity=trade.quantity,
                    price=trade.price,
                    realized_pnl=trade.realized_pnl,
                    executed_at=trade.executed_at,
                )
            )
        return result

    def order_to_schema(self, order: PaperOrder) -> OrderOut:
        return OrderOut(
            id=order.id,
            symbol=order.instrument.symbol,
            side=order.side,
            order_type=order.order_type,
            quantity=order.quantity,
            limit_price=order.limit_price,
            status=order.status,
            filled_price=order.filled_price,
            filled_at=order.filled_at,
            created_at=order.created_at,
        )

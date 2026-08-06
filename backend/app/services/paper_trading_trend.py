"""Daily paper-trading performance trends for portfolio proof."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Instrument, OrderSide, PaperTrade, PaperTradePlan
from app.services.paper_trading import PaperTradingService
from app.services.paper_trading_retention import (
    effective_retention_days,
    filter_closed_within_window,
    retention_window_start,
)

IST = ZoneInfo("Asia/Kolkata")


@dataclass
class ClosedTradeRow:
    symbol: str
    pattern_name: str
    closed_date: date
    recommendation_date: date | None
    status: str
    entry_price: float | None
    exit_price: float | None
    shares: int
    realized_pnl: float
    return_pct: float | None
    source: str


@dataclass
class DailyTrendRow:
    trade_date: date
    trades_closed: int
    wins: int
    losses: int
    day_pnl: float
    cumulative_pnl: float
    win_rate_pct: float
    avg_pnl_per_trade: float
    target_hits: int
    stop_hits: int


@dataclass
class PatternTrendRow:
    pattern_name: str
    trades: int
    wins: int
    total_pnl: float
    win_rate_pct: float
    avg_pnl: float


@dataclass
class PaperTradingTrendReport:
    as_of: date
    window_days: int
    window_start: date
    initial_cash: float
    cash_balance: float
    equity_value: float
    total_value: float
    unrealized_pnl: float
    total_realized_pnl: float
    total_return_pct: float
    open_positions: int
    trading_days: int
    total_closed_trades: int
    overall_win_rate_pct: float
    profitable_days: int
    losing_days: int
    flat_days: int
    best_day_pnl: float | None
    worst_day_pnl: float | None
    daily_rows: list[DailyTrendRow] = field(default_factory=list)
    closed_trades: list[ClosedTradeRow] = field(default_factory=list)
    pattern_rows: list[PatternTrendRow] = field(default_factory=list)
    trades_by_day: dict[date, list[ClosedTradeRow]] = field(default_factory=dict)


def _to_ist_date(ts: datetime | None) -> date | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=IST)
    else:
        ts = ts.astimezone(IST)
    return ts.date()


def _return_pct(entry: float | None, exit: float | None) -> float | None:
    if entry is None or exit is None or entry <= 0:
        return None
    return round((exit - entry) / entry * 100, 2)


def _build_daily_rows(closed: list[ClosedTradeRow]) -> list[DailyTrendRow]:
    by_day: dict[date, list[ClosedTradeRow]] = {}
    for row in closed:
        by_day.setdefault(row.closed_date, []).append(row)

    daily: list[DailyTrendRow] = []
    cumulative = 0.0
    for day in sorted(by_day):
        rows = by_day[day]
        day_pnl = round(sum(r.realized_pnl for r in rows), 2)
        wins = sum(1 for r in rows if r.realized_pnl > 0)
        losses = sum(1 for r in rows if r.realized_pnl < 0)
        target_hits = sum(1 for r in rows if r.status == "Target Hit")
        stop_hits = sum(1 for r in rows if r.status == "Stop Hit")
        cumulative = round(cumulative + day_pnl, 2)
        count = len(rows)
        win_rate = round(wins / count * 100, 1) if count else 0.0
        avg_pnl = round(day_pnl / count, 2) if count else 0.0
        daily.append(
            DailyTrendRow(
                trade_date=day,
                trades_closed=count,
                wins=wins,
                losses=losses,
                day_pnl=day_pnl,
                cumulative_pnl=cumulative,
                win_rate_pct=win_rate,
                avg_pnl_per_trade=avg_pnl,
                target_hits=target_hits,
                stop_hits=stop_hits,
            )
        )
    return daily


def _build_pattern_rows(closed: list[ClosedTradeRow]) -> list[PatternTrendRow]:
    buckets: dict[str, list[ClosedTradeRow]] = {}
    for row in closed:
        buckets.setdefault(row.pattern_name, []).append(row)

    pattern_rows: list[PatternTrendRow] = []
    for name, rows in buckets.items():
        total = round(sum(r.realized_pnl for r in rows), 2)
        wins = sum(1 for r in rows if r.realized_pnl > 0)
        count = len(rows)
        pattern_rows.append(
            PatternTrendRow(
                pattern_name=name,
                trades=count,
                wins=wins,
                total_pnl=total,
                win_rate_pct=round(wins / count * 100, 1) if count else 0.0,
                avg_pnl=round(total / count, 2) if count else 0.0,
            )
        )
    pattern_rows.sort(key=lambda row: row.total_pnl, reverse=True)
    return pattern_rows


class PaperTradingTrendService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.paper = PaperTradingService(session)

    async def build_report(
        self,
        *,
        window_days: int | None = None,
        now: datetime | None = None,
    ) -> PaperTradingTrendReport:
        days = effective_retention_days(window_days)
        current = now or datetime.now(IST)
        if current.tzinfo is None:
            current = current.replace(tzinfo=IST)
        else:
            current = current.astimezone(IST)
        as_of = current.date()
        window_start = retention_window_start(days=days, now=current)

        account_summary = await self.paper.get_account_summary()
        positions = await self.paper.list_positions()

        account = await self.paper.get_default_account()
        plans = (
            await self.session.scalars(
                select(PaperTradePlan)
                .where(PaperTradePlan.account_id == account.id)
                .options(selectinload(PaperTradePlan.instrument))
                .order_by(PaperTradePlan.recommendation_date.asc(), PaperTradePlan.id.asc())
            )
        ).all()

        plan_exit_orders = {p.exit_order_id for p in plans if p.exit_order_id is not None}
        closed: list[ClosedTradeRow] = []

        for plan in plans:
            if plan.realized_pnl is None:
                continue
            closed_date = _to_ist_date(plan.closed_at) or plan.recommendation_date
            entry = float(plan.entry_price) if plan.entry_price is not None else None
            exit_price = float(plan.exit_price) if plan.exit_price is not None else None
            pnl = float(plan.realized_pnl)
            closed.append(
                ClosedTradeRow(
                    symbol=plan.instrument.symbol,
                    pattern_name=plan.pattern_name or "Bracket",
                    closed_date=closed_date,
                    recommendation_date=plan.recommendation_date,
                    status=plan.status.value.replace("_", " ").title(),
                    entry_price=entry,
                    exit_price=exit_price,
                    shares=plan.shares,
                    realized_pnl=pnl,
                    return_pct=_return_pct(entry, exit_price),
                    source="bracket",
                )
            )

        orphan_sells = (
            await self.session.scalars(
                select(PaperTrade)
                .where(
                    PaperTrade.account_id == account.id,
                    PaperTrade.side == OrderSide.SELL,
                    PaperTrade.realized_pnl != 0,
                )
                .order_by(PaperTrade.executed_at.asc())
            )
        ).all()

        seen_order_ids = plan_exit_orders
        for trade in orphan_sells:
            if trade.order_id in seen_order_ids:
                continue
            instrument = await self.session.get(Instrument, trade.instrument_id)
            symbol = instrument.symbol if instrument else "UNKNOWN"
            closed_date = _to_ist_date(trade.executed_at) or date.today()
            pnl = float(trade.realized_pnl)
            closed.append(
                ClosedTradeRow(
                    symbol=symbol,
                    pattern_name="Manual",
                    closed_date=closed_date,
                    recommendation_date=None,
                    status="Closed",
                    entry_price=None,
                    exit_price=float(trade.price),
                    shares=trade.quantity,
                    realized_pnl=pnl,
                    return_pct=None,
                    source="manual",
                )
            )

        closed.sort(key=lambda row: (row.closed_date, row.symbol))
        closed = filter_closed_within_window(closed, days=days, now=current)
        daily_rows = _build_daily_rows(closed)
        pattern_rows = _build_pattern_rows(closed)

        trades_by_day: dict[date, list[ClosedTradeRow]] = {}
        for row in closed:
            trades_by_day.setdefault(row.closed_date, []).append(row)

        total_closed = len(closed)
        wins = sum(1 for r in closed if r.realized_pnl > 0)
        overall_win_rate = round(wins / total_closed * 100, 1) if total_closed else 0.0

        profitable_days = sum(1 for d in daily_rows if d.day_pnl > 0)
        losing_days = sum(1 for d in daily_rows if d.day_pnl < 0)
        flat_days = sum(1 for d in daily_rows if d.day_pnl == 0)

        day_pnls = [d.day_pnl for d in daily_rows]
        initial = float(account_summary.initial_cash)
        window_realized = round(sum(r.realized_pnl for r in closed), 2)
        total_return = round(window_realized / initial * 100, 2) if initial > 0 else 0.0

        return PaperTradingTrendReport(
            as_of=as_of,
            window_days=days,
            window_start=window_start,
            initial_cash=initial,
            cash_balance=float(account_summary.cash_balance),
            equity_value=float(account_summary.equity_value),
            total_value=float(account_summary.total_value),
            unrealized_pnl=float(account_summary.unrealized_pnl),
            total_realized_pnl=window_realized,
            total_return_pct=total_return,
            open_positions=len(positions),
            trading_days=len(daily_rows),
            total_closed_trades=total_closed,
            overall_win_rate_pct=overall_win_rate,
            profitable_days=profitable_days,
            losing_days=losing_days,
            flat_days=flat_days,
            best_day_pnl=max(day_pnls) if day_pnls else None,
            worst_day_pnl=min(day_pnls) if day_pnls else None,
            daily_rows=daily_rows,
            closed_trades=closed,
            pattern_rows=pattern_rows,
            trades_by_day=trades_by_day,
        )

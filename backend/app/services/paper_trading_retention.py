"""Retention and pruning for paper trading history."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.defaults import DEFAULT_PAPER_TRADING_RETENTION_DAYS
from app.models import (
    OrderStatus,
    PaperOrder,
    PaperTrade,
    PaperTradePlan,
    TradePlanStatus,
)
from app.services.market_calendar import is_post_session_eod_ready

IST = ZoneInfo("Asia/Kolkata")

TERMINAL_PLAN_STATUSES = (
    TradePlanStatus.TARGET_HIT,
    TradePlanStatus.STOP_HIT,
    TradePlanStatus.TIME_EXIT,
    TradePlanStatus.CANCELLED,
)


def effective_retention_days(days: int | None = None) -> int:
    if days is not None:
        return max(1, days)
    return max(1, int(settings.paper_trading_retention_days))


def retention_cutoff(*, days: int | None = None, now: datetime | None = None) -> datetime:
    """Inclusive window: records strictly before this IST timestamp are pruned."""
    current = now or datetime.now(IST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=IST)
    else:
        current = current.astimezone(IST)
    return current - timedelta(days=effective_retention_days(days))


def retention_window_start(*, days: int | None = None, now: datetime | None = None) -> date:
    return retention_cutoff(days=days, now=now).date()


def filter_closed_within_window(
    closed: list,
    *,
    days: int | None = None,
    now: datetime | None = None,
) -> list:
    """Keep closed trade rows whose closed_date falls within the rolling window."""
    start = retention_window_start(days=days, now=now)
    return [row for row in closed if row.closed_date >= start]


async def _referenced_order_ids(session: AsyncSession) -> set[int]:
    rows = await session.execute(
        select(PaperTradePlan.entry_order_id, PaperTradePlan.exit_order_id).where(
            or_(
                PaperTradePlan.entry_order_id.is_not(None),
                PaperTradePlan.exit_order_id.is_not(None),
            )
        )
    )
    referenced: set[int] = set()
    for entry_id, exit_id in rows.all():
        if entry_id is not None:
            referenced.add(int(entry_id))
        if exit_id is not None:
            referenced.add(int(exit_id))
    return referenced


async def prune_paper_trading_history(
    session: AsyncSession,
    *,
    retention_days: int | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """
    Delete paper trade plans, trades, and orders older than the retention window.

    Active bracket plans (PENDING_ENTRY / OPEN) are never removed.
    """
    cutoff = retention_cutoff(days=retention_days, now=now)
    cutoff_date = cutoff.date()

    plans_result = await session.execute(
        delete(PaperTradePlan).where(
            PaperTradePlan.status.in_(TERMINAL_PLAN_STATUSES),
            or_(
                and_(
                    PaperTradePlan.closed_at.is_not(None),
                    PaperTradePlan.closed_at < cutoff,
                ),
                and_(
                    PaperTradePlan.closed_at.is_(None),
                    PaperTradePlan.recommendation_date < cutoff_date,
                ),
            ),
        )
    )
    plans_deleted = int(plans_result.rowcount or 0)

    trades_result = await session.execute(
        delete(PaperTrade).where(PaperTrade.executed_at < cutoff)
    )
    trades_deleted = int(trades_result.rowcount or 0)

    referenced = await _referenced_order_ids(session)
    order_ts = func.coalesce(PaperOrder.filled_at, PaperOrder.created_at)
    orders_stmt = delete(PaperOrder).where(order_ts < cutoff)
    if referenced:
        orders_stmt = orders_stmt.where(PaperOrder.id.not_in(referenced))

    orders_result = await session.execute(orders_stmt)
    orders_deleted = int(orders_result.rowcount or 0)

    await session.commit()
    return {
        "plans_deleted": plans_deleted,
        "trades_deleted": trades_deleted,
        "orders_deleted": orders_deleted,
        "retention_days": effective_retention_days(retention_days),
        "cutoff": cutoff.isoformat(),
    }


async def prune_paper_trading_history_if_due(
    session: AsyncSession,
    *,
    retention_days: int | None = None,
    now: datetime | None = None,
) -> dict[str, int] | None:
    """
    Nightly prune after the session is done for review (3:45 PM IST on trade days).

    Skipped before cutoff so intraday sync does not drop same-day history early.
    """
    current = now or datetime.now(IST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=IST)
    else:
        current = current.astimezone(IST)

    if not is_post_session_eod_ready(current.date(), now=current):
        return None

    return await prune_paper_trading_history(
        session, retention_days=retention_days, now=current
    )

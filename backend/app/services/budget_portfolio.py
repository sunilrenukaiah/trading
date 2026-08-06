"""Budget-based portfolio view for paper trading (daily budget, not a large paper wallet)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import PaperAccount, PaperPosition
from app.schemas import PositionOut


@dataclass
class BudgetPortfolioView:
    budget_inr: float
    invested_cost: float
    cash_available: float
    equity_market_value: float
    total_value: float
    unrealized_pnl: float


@dataclass
class BaseBudgetAvailableView:
    """Deployable cash from the morning base budget (profits are not reinvested)."""

    morning_budget_inr: float
    invested_cost: float
    session_realized_pnl: float
    cash_available: float


def budget_from_settings() -> float:
    return float(settings.daily_trading_budget_inr)


def compute_budget_view(
    budget_inr: float,
    positions: list[PositionOut],
) -> BudgetPortfolioView:
    """Cash available = budget minus cost basis of open positions (recommendations + manual buys)."""
    invested_cost = sum(float(p.avg_cost * p.quantity) for p in positions)
    equity_market = sum(float(p.market_value or 0) for p in positions)
    cash_available = round(budget_inr - invested_cost, 2)
    unrealized = round(equity_market - invested_cost, 2)
    total = round(cash_available + equity_market, 2)
    return BudgetPortfolioView(
        budget_inr=budget_inr,
        invested_cost=round(invested_cost, 2),
        cash_available=cash_available,
        equity_market_value=round(equity_market, 2),
        total_value=total,
        unrealized_pnl=unrealized,
    )


def portfolio_total_at_cost(
    invested_cost: float,
    cash_available: float,
    realized_after_tax: float,
) -> float:
    """Invested + cash available + cumulative realized P&L after tax."""
    return round(invested_cost + cash_available + realized_after_tax, 2)


def portfolio_total_with_unrealized(
    invested_cost: float,
    cash_available: float,
    realized_after_tax: float,
    unrealized_pnl: float,
) -> float:
    """Total at cost plus mark-to-market uplift on open positions."""
    return round(
        invested_cost + cash_available + realized_after_tax + unrealized_pnl, 2
    )


async def normalize_legacy_paper_account(session: AsyncSession, budget_inr: float) -> None:
    """One-time fix for accounts seeded with the old ₹10L paper wallet."""
    account = await session.scalar(select(PaperAccount).limit(1))
    if not account:
        return
    if float(account.initial_cash) <= budget_inr * 1.5:
        return

    positions = (
        await session.scalars(
            select(PaperPosition).where(
                PaperPosition.account_id == account.id,
                PaperPosition.quantity > 0,
            )
        )
    ).all()
    invested = sum(float(p.avg_cost * p.quantity) for p in positions)
    account.initial_cash = Decimal(str(budget_inr))
    account.cash_balance = Decimal(str(round(budget_inr - invested, 2)))
    await session.commit()


def compute_base_budget_available(
    morning_budget_inr: float,
    positions: list[PositionOut],
    session_realized_pnl: float,
) -> BaseBudgetAvailableView:
    """
    Cash available for new mid-day trades from the morning base budget only.

    Profits are withheld (not reinvested); closed losses reduce deployable capital.
    """
    invested_cost = round(sum(float(p.avg_cost * p.quantity) for p in positions), 2)
    realized = round(float(session_realized_pnl), 2)
    withheld = max(realized, 0.0) - min(realized, 0.0)
    cash_available = round(morning_budget_inr - invested_cost - withheld, 2)
    return BaseBudgetAvailableView(
        morning_budget_inr=round(float(morning_budget_inr), 2),
        invested_cost=invested_cost,
        session_realized_pnl=realized,
        cash_available=max(cash_available, 0.0),
    )


def validate_buy_against_budget(
    budget_inr: float,
    positions: list[PositionOut],
    order_cost: float,
    *,
    session_realized_pnl: float | None = None,
) -> None:
    if session_realized_pnl is not None:
        view = compute_base_budget_available(budget_inr, positions, session_realized_pnl)
        available = view.cash_available
        detail = (
            f"morning budget ₹{view.morning_budget_inr:,.2f}, "
            f"invested ₹{view.invested_cost:,.2f}, "
            f"today's realized P&L ₹{view.session_realized_pnl:,.2f}"
        )
    else:
        view = compute_budget_view(budget_inr, positions)
        available = view.cash_available
        detail = (
            f"daily budget ₹{budget_inr:,.2f}, invested ₹{view.invested_cost:,.2f}"
        )
    if order_cost > available + 0.01:
        raise ValueError(
            f"Insufficient budget: need ₹{order_cost:,.2f} but only "
            f"₹{max(available, 0):,.2f} available ({detail})"
        )

"""Budget-based portfolio view for paper trading."""

from __future__ import annotations

import pytest

from app.schemas import PositionOut
from app.services.budget_portfolio import (
    compute_base_budget_available,
    compute_budget_view,
    portfolio_total_at_cost,
    portfolio_total_with_unrealized,
    validate_buy_against_budget,
)


def _pos(symbol: str, qty: int, cost: float, mkt: float) -> PositionOut:
    from decimal import Decimal

    return PositionOut(
        symbol=symbol,
        name=symbol,
        quantity=qty,
        avg_cost=Decimal(str(cost)),
        mark_price=Decimal(str(mkt)),
        market_value=Decimal(str(mkt * qty)),
        unrealized_pnl=Decimal(str((mkt - cost) * qty)),
    )


@pytest.mark.quick
def test_compute_budget_view_empty() -> None:
    view = compute_budget_view(50_000, [])
    assert view.cash_available == 50_000
    assert view.invested_cost == 0
    assert view.total_value == 50_000


@pytest.mark.quick
def test_compute_budget_view_with_positions() -> None:
    positions = [_pos("RELIANCE", 1, 2500, 2600)]
    view = compute_budget_view(50_000, positions)
    assert view.invested_cost == 2500
    assert view.cash_available == 47_500
    assert view.equity_market_value == 2600
    assert view.unrealized_pnl == 100


@pytest.mark.quick
def test_validate_buy_against_budget() -> None:
    positions = [_pos("TCS", 2, 3500, 3600)]
    validate_buy_against_budget(50_000, positions, 40_000)
    with pytest.raises(ValueError, match="Insufficient budget"):
        validate_buy_against_budget(50_000, positions, 44_000)


@pytest.mark.quick
def test_portfolio_total_value_formulas() -> None:
    positions = [_pos("RELIANCE", 1, 2500, 2600)]
    view = compute_budget_view(50_000, positions)
    realized_after_tax = 60.57
    assert portfolio_total_at_cost(
        view.invested_cost, view.cash_available, realized_after_tax
    ) == pytest.approx(50_060.57)
    assert portfolio_total_with_unrealized(
        view.invested_cost,
        view.cash_available,
        realized_after_tax,
        view.unrealized_pnl,
    ) == pytest.approx(50_160.57)


@pytest.mark.quick
def test_compute_base_budget_available_with_profit() -> None:
    positions = [_pos("RELIANCE", 10, 3000, 3100)]
    view = compute_base_budget_available(50_000, positions, 5_000)
    assert view.invested_cost == 30_000
    assert view.cash_available == 15_000


@pytest.mark.quick
def test_compute_base_budget_available_with_loss() -> None:
    positions = [_pos("RELIANCE", 5, 4000, 3900)]
    view = compute_base_budget_available(50_000, positions, -2_000)
    assert view.invested_cost == 20_000
    assert view.cash_available == 28_000


@pytest.mark.quick
def test_validate_buy_against_base_budget() -> None:
    positions = [_pos("TCS", 10, 3000, 3100)]
    with pytest.raises(ValueError, match="Insufficient budget"):
        validate_buy_against_budget(
            50_000,
            positions,
            20_000,
            session_realized_pnl=5_000,
        )

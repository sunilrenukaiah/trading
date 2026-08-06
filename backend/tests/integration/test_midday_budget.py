"""Mid-day base budget context tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.schemas import PositionOut
from app.services.budget_portfolio import compute_base_budget_available


def _pos(symbol: str, qty: int, cost: float) -> PositionOut:
    return PositionOut(
        symbol=symbol,
        name=symbol,
        quantity=qty,
        avg_cost=Decimal(str(cost)),
        mark_price=Decimal(str(cost)),
        market_value=Decimal(str(cost * qty)),
        unrealized_pnl=Decimal("0"),
    )


@pytest.mark.quick
def test_base_budget_available_no_profit_no_positions() -> None:
    view = compute_base_budget_available(50_000, [], 0.0)
    assert view.cash_available == 50_000


@pytest.mark.quick
def test_base_budget_available_withheld_profit() -> None:
    positions = [_pos("TCS", 10, 3000)]
    view = compute_base_budget_available(50_000, positions, 5_000)
    assert view.invested_cost == 30_000
    assert view.cash_available == 15_000


@pytest.mark.quick
@pytest.mark.asyncio
async def test_midday_budget_context_uses_morning_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    from ui import helpers

    async def fake_load_cached():
        return (None, None, 75_000.0, 80.0, None)

    async def fake_positions():
        return [_pos("RELIANCE", 5, 2000)]

    class _Paper:
        async def day_realized_pnl_from_trades(self, _trade_date: date):
            return Decimal("2500")

    class _Ctx:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *args):
            return None

    def fake_ui_session():
        return _Ctx()

    monkeypatch.setattr(
        "app.services.recommendation_cache.load_cached_recommendations_for_ui",
        fake_load_cached,
    )
    monkeypatch.setattr(helpers, "_positions", fake_positions)
    monkeypatch.setattr(helpers, "ui_session", fake_ui_session)
    monkeypatch.setattr(helpers, "PaperTradingService", lambda _s: _Paper())
    monkeypatch.setattr(
        "app.services.market_calendar.current_session_date",
        lambda: date(2026, 8, 5),
    )

    ctx = await helpers._midday_budget_context()

    assert ctx.morning_budget_inr == 75_000
    assert ctx.invested_cost == 10_000
    assert ctx.session_realized_pnl == 2_500
    assert ctx.available_inr == 62_500  # 75000 - 10000 - 2500

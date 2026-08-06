"""Positions table color grading and sorting."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.schemas import PositionOut, PositionSource
from ui.positions_display import (
    assign_current_price_colors,
    assign_target_gap_colors,
    bracket_progress,
    build_position_rows,
    sort_position_rows,
    PositionTableRow,
)


def _row(
    symbol: str,
    *,
    current: float,
    target: float,
    stop: float,
    pnl: float = 0.0,
) -> PositionTableRow:
    return PositionTableRow(
        position=PositionOut(
            symbol=symbol,
            name=symbol,
            quantity=1,
            avg_cost=Decimal("100"),
            mark_price=Decimal(str(current)),
            market_value=Decimal(str(current)),
            unrealized_pnl=Decimal(str(pnl)),
            source=PositionSource.RECOMMENDATION,
        ),
        symbol=symbol,
        is_recommendation=True,
        quantity=1,
        avg_cost=100.0,
        today_open=None,
        prev_close=None,
        session_high=None,
        current_price=current,
        live_price=current,
        live_session_high=None,
        market_value=current,
        unrealized_pnl=pnl,
        target=target,
        stop_loss=stop,
    )


@pytest.mark.quick
def test_bracket_progress_midpoint() -> None:
    assert bracket_progress(150.0, 100.0, 200.0) == 0.5


@pytest.mark.quick
def test_target_proximity_grades_strongest_green_for_closest() -> None:
    rows = [
        _row("A", current=198.0, target=200.0, stop=100.0),
        _row("B", current=190.0, target=200.0, stop=100.0),
        _row("C", current=160.0, target=200.0, stop=100.0),
    ]
    assign_current_price_colors(rows)
    assert rows[0].current_price_color.startswith("hsl(122")
    assert rows[0].current_price_color != rows[1].current_price_color
    assert rows[1].current_price_color != rows[2].current_price_color


@pytest.mark.quick
def test_stop_proximity_grades_strongest_red_for_closest() -> None:
    rows = [
        _row("A", current=102.0, target=200.0, stop=100.0),
        _row("B", current=120.0, target=200.0, stop=100.0),
        _row("C", current=140.0, target=200.0, stop=100.0),
    ]
    assign_current_price_colors(rows)
    assert rows[0].current_price_color.startswith("hsl(0")
    assert rows[0].current_price_color != rows[1].current_price_color


@pytest.mark.quick
def test_sort_defaults_unrealized_pnl_descending() -> None:
    rows = [
        _row("LOW", current=100.0, target=120.0, stop=90.0, pnl=10.0),
        _row("HIGH", current=100.0, target=120.0, stop=90.0, pnl=50.0),
        _row("MID", current=100.0, target=120.0, stop=90.0, pnl=25.0),
    ]
    ordered = sort_position_rows(rows, "unrealized_pnl", ascending=False)
    assert [r.symbol for r in ordered] == ["HIGH", "MID", "LOW"]


@pytest.mark.quick
def test_build_position_rows_uses_bracket_levels() -> None:
    position = PositionOut(
        symbol="NTPC",
        name="NTPC",
        quantity=2,
        avg_cost=Decimal("340"),
        mark_price=Decimal("345"),
        market_value=Decimal("690"),
        unrealized_pnl=Decimal("10"),
        source=PositionSource.RECOMMENDATION,
    )

    def snapshot(pos, quotes):
        return 345.0, 690.0, 10.0

    rows = build_position_rows(
        [position],
        {"NTPC": {"ltp": 346.0, "open": 342.0, "prev_close": 345.0, "high": 347.5}},
        {"NTPC": (350.1, 338.39)},
        snapshot_fn=snapshot,
    )
    assert rows[0].target == 350.1
    assert rows[0].stop_loss == 338.39
    assert rows[0].today_open == 342.0
    assert rows[0].prev_close == 345.0
    assert rows[0].session_high == 347.5
    assert rows[0].live_price == 346.0


@pytest.mark.quick
def test_open_price_html_shows_prev_close_in_brackets() -> None:
    from ui.positions_display import open_price_html

    row = _row("X", current=100.0, target=120.0, stop=90.0)
    row.today_open = 101.0
    row.prev_close = 99.5
    text = open_price_html(row, format_inr=lambda v: f"₹{v:.2f}")
    assert text == "₹101.00 (₹99.50)"


@pytest.mark.quick
def test_session_high_html_marks_live() -> None:
    from ui.positions_display import session_high_html

    row = _row("X", current=100.0, target=120.0, stop=90.0)
    row.session_high = 105.0
    row.live_session_high = 105.0
    assert session_high_html(row, format_inr=lambda v: f"₹{v:.2f}") == "₹105.00 *"


@pytest.mark.quick
def test_target_gap_per_share_and_total() -> None:
    row = _row("NHPC", current=80.01, target=80.03, stop=76.0, pnl=5.0)
    row.quantity = 10
    assign_target_gap_colors([row])
    assert row.target_gap_per_share == pytest.approx(0.02)
    assert row.target_gap_total == pytest.approx(0.2)


@pytest.mark.quick
def test_target_gap_red_when_unrealized_loss() -> None:
    rows = [
        _row("LOSS", current=340.0, target=350.0, stop=330.0, pnl=-10.0),
        _row("WIN", current=340.0, target=350.0, stop=330.0, pnl=10.0),
    ]
    assign_target_gap_colors(rows)
    assert rows[0].target_gap_color == "#c62828"
    assert rows[1].target_gap_color.startswith("hsl(122")


@pytest.mark.quick
def test_target_gap_greener_when_closer_to_target() -> None:
    rows = [
        _row("CLOSE", current=349.0, target=350.0, stop=330.0, pnl=5.0),
        _row("FAR", current=340.0, target=350.0, stop=330.0, pnl=5.0),
    ]
    assign_target_gap_colors(rows)
    assert rows[0].target_gap_color != rows[1].target_gap_color
    assert rows[0].target_gap_per_share == pytest.approx(1.0)
    assert rows[1].target_gap_per_share == pytest.approx(10.0)

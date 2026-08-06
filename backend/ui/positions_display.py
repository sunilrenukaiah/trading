"""Positions table helpers: row building, bracket proximity colors, sorting."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.schemas import PositionOut, PositionSource
from app.services.live_quotes import PositionLiveQuote, live_quote_ltp

REC_SYMBOL_COLOR = "#1565c0"
MANUAL_SYMBOL_COLOR = "#212121"
NEUTRAL_PRICE_COLOR = "#424242"
DEFAULT_PRICE_COLOR = "#212121"

SORTABLE_COLUMNS = (
    "symbol",
    "quantity",
    "avg_cost",
    "today_open",
    "session_high",
    "current_price",
    "market_value",
    "unrealized_pnl",
    "target",
    "target_gap_per_share",
    "stop_loss",
)

COLUMN_LABELS = {
    "symbol": "Symbol",
    "quantity": "Qty",
    "avg_cost": "Avg cost",
    "today_open": "Open price",
    "session_high": "Today's high",
    "current_price": "Current price",
    "market_value": "Market value",
    "unrealized_pnl": "Unrealized P&L",
    "target": "Target",
    "target_gap_per_share": "To target",
    "stop_loss": "Stop loss",
}


@dataclass
class PositionTableRow:
    position: PositionOut
    symbol: str
    is_recommendation: bool
    quantity: int
    avg_cost: float
    today_open: float | None
    prev_close: float | None
    session_high: float | None
    current_price: float | None
    live_price: float | None
    live_session_high: float | None
    market_value: float | None
    unrealized_pnl: float | None
    target: float | None
    stop_loss: float | None
    target_gap_per_share: float | None = None
    target_gap_total: float | None = None
    current_price_color: str = DEFAULT_PRICE_COLOR
    target_gap_color: str = DEFAULT_PRICE_COLOR


def bracket_progress(current: float, stop: float, target: float) -> float | None:
    """0 = at stop, 1 = at target."""
    span = target - stop
    if span <= 0:
        return None
    return max(0.0, min(1.0, (current - stop) / span))


def assign_current_price_colors(rows: list[PositionTableRow]) -> None:
    """Grade green toward target and red toward stop; closest names are strongest."""
    target_side: list[tuple[int, float]] = []
    stop_side: list[tuple[int, float]] = []

    for idx, row in enumerate(rows):
        if row.current_price is None or row.target is None or row.stop_loss is None:
            row.current_price_color = DEFAULT_PRICE_COLOR
            continue

        progress = bracket_progress(row.current_price, row.stop_loss, row.target)
        if progress is None:
            row.current_price_color = NEUTRAL_PRICE_COLOR
            continue

        row.current_price_color = NEUTRAL_PRICE_COLOR
        if progress >= 0.5:
            target_side.append((idx, progress))
        else:
            stop_side.append((idx, progress))

    target_side.sort(key=lambda item: item[1], reverse=True)
    stop_side.sort(key=lambda item: item[1])

    for rank, (idx, _progress) in enumerate(target_side):
        intensity = 1.0 if len(target_side) == 1 else 1.0 - rank / (len(target_side) - 1)
        saturation = 55 + intensity * 35
        lightness = 28 + intensity * 22
        rows[idx].current_price_color = f"hsl(122, {saturation:.0f}%, {lightness:.0f}%)"

    for rank, (idx, _progress) in enumerate(stop_side):
        intensity = 1.0 if len(stop_side) == 1 else 1.0 - rank / (len(stop_side) - 1)
        saturation = 60 + intensity * 35
        lightness = 32 + intensity * 18
        rows[idx].current_price_color = f"hsl(0, {saturation:.0f}%, {lightness:.0f}%)"


def _compute_target_gaps(row: PositionTableRow) -> None:
    if row.current_price is None or row.target is None:
        row.target_gap_per_share = None
        row.target_gap_total = None
        return
    row.target_gap_per_share = round(row.target - row.current_price, 2)
    row.target_gap_total = round(row.target_gap_per_share * row.quantity, 2)


def assign_target_gap_colors(rows: list[PositionTableRow]) -> None:
    """Red when unrealized P&L is negative; otherwise greener when closer to target."""
    in_loss: list[tuple[int, float]] = []
    in_profit: list[tuple[int, float]] = []

    for idx, row in enumerate(rows):
        _compute_target_gaps(row)
        if row.target_gap_per_share is None:
            row.target_gap_color = DEFAULT_PRICE_COLOR
            continue

        gap = row.target_gap_per_share
        pnl = row.unrealized_pnl
        if pnl is not None and pnl < 0:
            in_loss.append((idx, gap))
        else:
            in_profit.append((idx, gap))

    for idx, _gap in in_loss:
        rows[idx].target_gap_color = "#c62828"

    in_profit.sort(key=lambda item: item[1])
    for rank, (idx, gap) in enumerate(in_profit):
        if gap <= 0:
            rows[idx].target_gap_color = "hsl(122, 85%, 26%)"
            continue
        intensity = 1.0 if len(in_profit) == 1 else 1.0 - rank / (len(in_profit) - 1)
        saturation = 50 + intensity * 40
        lightness = 30 + intensity * 20
        rows[idx].target_gap_color = f"hsl(122, {saturation:.0f}%, {lightness:.0f}%)"


def build_position_rows(
    positions: list[PositionOut],
    live_quotes: dict[str, object],
    bracket_levels: dict[str, tuple[float, float]],
    *,
    snapshot_fn,
) -> list[PositionTableRow]:
    rows: list[PositionTableRow] = []
    for position in positions:
        parsed = PositionLiveQuote.from_cache(live_quotes.get(position.symbol))
        current, market_value, unrealized = snapshot_fn(position, live_quotes)
        target_sl = bracket_levels.get(position.symbol)
        target = target_sl[0] if target_sl else None
        stop = target_sl[1] if target_sl else None
        mark = float(position.mark_price) if position.mark_price is not None else None
        today_open = parsed.today_open if parsed else None
        prev_close = parsed.prev_close if parsed else None
        session_high = parsed.session_high if parsed else None
        if today_open is None and mark is not None and prev_close is None:
            prev_close = mark
        rows.append(
            PositionTableRow(
                position=position,
                symbol=position.symbol,
                is_recommendation=position.source == PositionSource.RECOMMENDATION,
                quantity=position.quantity,
                avg_cost=float(position.avg_cost),
                today_open=today_open,
                prev_close=prev_close,
                session_high=session_high,
                current_price=current,
                live_price=live_quote_ltp(live_quotes, position.symbol),
                live_session_high=session_high if parsed else None,
                market_value=market_value,
                unrealized_pnl=unrealized,
                target=target,
                stop_loss=stop,
            )
        )
    assign_current_price_colors(rows)
    assign_target_gap_colors(rows)
    return rows


def sort_position_rows(
    rows: list[PositionTableRow],
    column: str,
    *,
    ascending: bool = False,
) -> list[PositionTableRow]:
    if column not in SORTABLE_COLUMNS:
        return list(rows)

    def sort_value(row: PositionTableRow) -> Any:
        value = getattr(row, column)
        if value is None:
            return None
        if column == "symbol":
            return value.lower()
        return value

    with_values = [row for row in rows if sort_value(row) is not None]
    without_values = [row for row in rows if sort_value(row) is None]
    ordered = sorted(with_values, key=sort_value, reverse=not ascending)
    return ordered + without_values


def symbol_html(row: PositionTableRow) -> str:
    color = REC_SYMBOL_COLOR if row.is_recommendation else MANUAL_SYMBOL_COLOR
    weight = "700" if row.is_recommendation else "600"
    return f'<span style="color:{color};font-weight:{weight};">{row.symbol}</span>'


def open_price_html(row: PositionTableRow, *, format_inr) -> str:
    if row.today_open is not None:
        open_text = format_inr(row.today_open)
    elif row.prev_close is not None:
        open_text = "—"
    else:
        return "—"
    if row.prev_close is not None:
        return f"{open_text} ({format_inr(row.prev_close)})"
    return open_text


def session_high_html(row: PositionTableRow, *, format_inr) -> str:
    if row.session_high is None:
        return "—"
    suffix = " *" if row.live_session_high is not None else ""
    return f"{format_inr(row.session_high)}{suffix}"


def current_price_html(row: PositionTableRow, *, format_inr) -> str:
    if row.live_price is not None:
        text = f"{format_inr(row.live_price)} *"
    elif row.current_price is not None:
        text = format_inr(row.current_price)
    else:
        return "—"
    return (
        f'<span style="color:{row.current_price_color};font-weight:600;">{text}</span>'
    )


def target_gap_html(row: PositionTableRow, *, format_inr) -> str:
    if row.target_gap_per_share is None:
        return "—"
    if row.target_gap_per_share <= 0:
        text = f"{format_inr(0)} ({format_inr(0)})"
    else:
        text = f"{format_inr(row.target_gap_per_share)} ({format_inr(row.target_gap_total or 0.0)})"
    return (
        f'<span style="color:{row.target_gap_color};font-weight:600;">{text}</span>'
    )

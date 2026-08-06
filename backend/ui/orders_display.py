"""Orders tab helpers."""

from __future__ import annotations

from typing import Any

from app.services.live_quotes import live_quote_ltp


def resolve_order_current_price(
    symbol: str,
    *,
    live_quotes: dict[str, Any],
    close_by_symbol: dict[str, float],
) -> tuple[float | None, bool]:
    """Return (price, is_live). Prefer cached LTP; fall back to stored last close."""
    live = live_quote_ltp(live_quotes, symbol)
    if live is not None:
        return live, True
    close = close_by_symbol.get(symbol)
    if close is not None:
        return close, False
    return None, False

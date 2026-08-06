"""Symbol history chart dialog tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.schemas import CandleOut
from ui.symbol_history_chart import build_symbol_history_chart, _close_symbol_history_chart_dialog


@pytest.mark.quick
def test_build_symbol_history_chart_renders_candles() -> None:
    candles = [
        CandleOut(
            trade_date=date(2026, 7, 28),
            open=Decimal("100"),
            high=Decimal("105"),
            low=Decimal("99"),
            close=Decimal("104"),
            volume=1000,
        ),
        CandleOut(
            trade_date=date(2026, 7, 29),
            open=Decimal("104"),
            high=Decimal("108"),
            low=Decimal("103"),
            close=Decimal("107"),
            volume=1200,
        ),
    ]
    fig = build_symbol_history_chart("RELIANCE", candles, days=30)
    assert "RELIANCE — 30-day chart" in fig.layout.title.text
    assert any(type(t).__name__ == "Candlestick" for t in fig.data)


@pytest.mark.quick
def test_close_symbol_history_chart_dialog_clears_open_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ui import symbol_history_chart as chart_mod

    session: dict[str, object] = {
        chart_mod._DIALOG_SESSION_KEY: {"symbol": "INFY", "days": 30},
        chart_mod._DIALOG_OPEN_KEY: True,
        f"{chart_mod._CANDLES_CACHE_PREFIX}INFY_30": [],
        "symbol_hist_days_INFY": 30,
    }
    monkeypatch.setattr(chart_mod.st, "session_state", session, raising=False)
    chart_mod._close_symbol_history_chart_dialog("INFY")
    assert chart_mod._DIALOG_OPEN_KEY not in session
    assert chart_mod._DIALOG_SESSION_KEY not in session

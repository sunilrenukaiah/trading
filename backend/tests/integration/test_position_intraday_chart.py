"""Position intraday chart tests."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.services.intraday_chart import (
    IntradayBar,
    PositionIntradayContext,
    _synthetic_bars_from_quote,
    resample_intraday_bars,
)
from ui.position_intraday_chart import (
    build_position_intraday_chart,
    pattern_targets_dataframe,
    position_summary_dataframe,
)

IST = ZoneInfo("Asia/Kolkata")


@pytest.mark.quick
async def test_prior_session_close_from_db_uses_previous_trading_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import date
    from decimal import Decimal
    from unittest.mock import AsyncMock, MagicMock

    from app.services.intraday_chart import _prior_session_close_from_db

    prior_candle = MagicMock()
    prior_candle.close = Decimal("1488.50")

    session = AsyncMock()
    session.scalar = AsyncMock(side_effect=[prior_candle, None])

    fixed_today = date(2026, 7, 30)
    monkeypatch.setattr(
        "app.services.intraday_chart.is_trading_day",
        lambda d: d == fixed_today,
    )
    monkeypatch.setattr(
        "app.services.intraday_chart.get_previous_trading_day",
        lambda d: date(2026, 7, 29),
    )

    result = await _prior_session_close_from_db(session, 1, as_of=fixed_today)

    assert result == 1488.5
    session.scalar.assert_awaited()


@pytest.mark.quick
def test_synthetic_bars_from_quote() -> None:
    bars = _synthetic_bars_from_quote({"open": 100.0, "last": 105.0})
    assert len(bars) == 2
    assert bars[0].close == 100.0
    assert bars[1].close == 105.0


@pytest.mark.quick
def test_resample_intraday_bars_15m() -> None:
    base = datetime(2026, 7, 30, 9, 15, tzinfo=IST)
    bars = [
        IntradayBar(base, 100, 101, 99, 100.5),
        IntradayBar(base.replace(minute=20), 100.5, 102, 100, 101),
        IntradayBar(base.replace(minute=25), 101, 103, 100.5, 102),
    ]
    resampled = resample_intraday_bars(bars, 15)
    assert len(resampled) == 1
    assert resampled[0].open == 100
    assert resampled[0].close == 102
    assert resampled[0].high == 103
    assert resampled[0].low == 99


@pytest.mark.quick
def test_resample_interval_changes_candle_count() -> None:
    base = datetime(2026, 7, 30, 9, 15, tzinfo=IST)
    bars = [
        IntradayBar(base.replace(minute=15 + i * 5), 100 + i, 101 + i, 99 + i, 100.5 + i)
        for i in range(6)
    ]
    assert len(resample_intraday_bars(bars, 15)) == 2
    assert len(resample_intraday_bars(bars, 5)) == 6


@pytest.mark.quick
def test_build_position_intraday_chart_interval_subtitle() -> None:
    now = datetime.now(IST)
    open_ts = now.replace(hour=9, minute=15, second=0, microsecond=0)
    ctx = PositionIntradayContext(
        symbol="SUZLON",
        pattern_name="Inverted Hammer",
        prev_close=48.0,
        today_open=48.5,
        today_high=49.0,
        today_low=47.5,
        current_price=48.8,
        target_price=51.2,
        stop_loss_price=45.8,
        model_target_price=54.5,
        resistance=55.5,
        entry_price=48.0,
        bars=[IntradayBar(open_ts.replace(minute=15 + i * 5), 48, 49, 47.5, 48.5) for i in range(6)],
    )
    fig_5m = build_position_intraday_chart(
        ctx,
        bars=resample_intraday_bars(ctx.bars, 5),
        interval_label="5m",
    )
    fig_15m = build_position_intraday_chart(
        ctx,
        bars=resample_intraday_bars(ctx.bars, 15),
        interval_label="15m",
    )
    assert "5m candles" in fig_5m.layout.title.text
    assert "15m candles" in fig_15m.layout.title.text
    candle_5m = next(t for t in fig_5m.data if type(t).__name__ == "Candlestick")
    candle_15m = next(t for t in fig_15m.data if type(t).__name__ == "Candlestick")
    assert len(candle_5m.x) == 6
    assert len(candle_15m.x) == 2


@pytest.mark.quick
def test_close_position_chart_dialog_clears_open_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    import streamlit as st

    from ui import position_intraday_chart as chart_mod

    session: dict[str, object] = {
        chart_mod._DIALOG_SESSION_KEY: {"symbol": "SUZLON", "live_price": 48.0, "mark_price": None},
        chart_mod._DIALOG_OPEN_KEY: True,
        f"{chart_mod._CONTEXT_SESSION_PREFIX}SUZLON": object(),
        "pos_chart_interval_SUZLON": "5m",
    }
    monkeypatch.setattr(chart_mod.st, "session_state", session, raising=False)
    chart_mod._close_position_chart_dialog("SUZLON")
    assert chart_mod._DIALOG_OPEN_KEY not in session
    assert chart_mod._DIALOG_SESSION_KEY not in session


@pytest.mark.quick
def test_build_position_intraday_chart_includes_target_markers() -> None:
    now = datetime.now(IST)
    open_ts = now.replace(hour=9, minute=20, second=0, microsecond=0)
    ctx = PositionIntradayContext(
        symbol="INFY",
        pattern_name="Hammer",
        prev_close=1480.0,
        today_open=1490.0,
        today_high=1510.0,
        today_low=1485.0,
        current_price=1505.0,
        target_price=1575.0,
        stop_loss_price=1450.0,
        model_target_price=1600.0,
        resistance=1550.0,
        entry_price=1495.0,
        bars=[
            IntradayBar(open_ts, 1490, 1495, 1488, 1492),
            IntradayBar(now, 1500, 1510, 1498, 1505),
        ],
    )
    fig = build_position_intraday_chart(ctx, interval_label="15m")
    trace_types = {type(t).__name__ for t in fig.data}
    assert "Candlestick" in trace_types
    trace_names = {t.name for t in fig.data}
    assert "Actual target" in trace_names
    assert "Stop loss" in trace_names
    assert "Model target" in trace_names
    assert "Prev close" in trace_names
    assert fig.layout.legend.orientation == "v"
    candlestick = next(t for t in fig.data if type(t).__name__ == "Candlestick")
    assert candlestick.showlegend is False

    summary = position_summary_dataframe(ctx)
    assert summary.iloc[0]["Metric"] == "Pattern"
    targets = pattern_targets_dataframe(ctx)
    assert len(targets) == 5

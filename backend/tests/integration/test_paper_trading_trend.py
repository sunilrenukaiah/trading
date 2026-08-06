"""Paper trading trend aggregation tests."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.services.paper_trading_trend import (
    ClosedTradeRow,
    _build_daily_rows,
    _build_pattern_rows,
)


def _trade(
    symbol: str,
    *,
    closed_date: date,
    pnl: float,
    pattern: str = "Hammer",
    status: str = "Target Hit",
) -> ClosedTradeRow:
    return ClosedTradeRow(
        symbol=symbol,
        pattern_name=pattern,
        closed_date=closed_date,
        recommendation_date=closed_date,
        status=status,
        entry_price=100.0,
        exit_price=100.0 + pnl,
        shares=10,
        realized_pnl=pnl,
        return_pct=pnl,
        source="bracket",
    )


@pytest.mark.quick
def test_build_daily_rows_cumulative_pnl() -> None:
    closed = [
        _trade("AAA", closed_date=date(2026, 7, 28), pnl=500.0),
        _trade("BBB", closed_date=date(2026, 7, 28), pnl=-200.0),
        _trade("CCC", closed_date=date(2026, 7, 29), pnl=300.0),
    ]
    daily = _build_daily_rows(closed)
    assert len(daily) == 2
    assert daily[0].trade_date == date(2026, 7, 28)
    assert daily[0].day_pnl == 300.0
    assert daily[0].wins == 1
    assert daily[0].losses == 1
    assert daily[1].cumulative_pnl == 600.0


@pytest.mark.quick
def test_filter_closed_within_window_for_trend() -> None:
    from app.services.paper_trading_retention import filter_closed_within_window

    now = datetime(2026, 7, 30, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    closed = [
        _trade("AAA", closed_date=date(2026, 6, 1), pnl=100.0),
        _trade("BBB", closed_date=date(2026, 7, 20), pnl=200.0),
    ]
    filtered = filter_closed_within_window(closed, days=30, now=now)
    assert len(filtered) == 1
    assert filtered[0].symbol == "BBB"


@pytest.mark.quick
def test_daily_trend_dataframe_date_sorts_chronologically() -> None:
    from app.services.paper_trading_trend import DailyTrendRow, PaperTradingTrendReport
    from ui.paper_trading_trend_display import daily_trend_dataframe

    report = PaperTradingTrendReport(
        as_of=date(2026, 8, 5),
        window_days=30,
        window_start=date(2026, 7, 1),
        initial_cash=50_000.0,
        cash_balance=50_000.0,
        equity_value=0.0,
        total_value=50_000.0,
        unrealized_pnl=0.0,
        total_realized_pnl=0.0,
        total_return_pct=0.0,
        open_positions=0,
        trading_days=5,
        total_closed_trades=0,
        overall_win_rate_pct=0.0,
        profitable_days=0,
        losing_days=0,
        flat_days=0,
        best_day_pnl=300.0,
        worst_day_pnl=-50.0,
        daily_rows=[
            DailyTrendRow(date(2026, 7, 30), 1, 1, 0, 100.0, 100.0, 100.0, 100.0, 0, 0),
            DailyTrendRow(date(2026, 7, 31), 1, 1, 0, 200.0, 300.0, 100.0, 200.0, 0, 0),
            DailyTrendRow(date(2026, 8, 3), 1, 1, 0, 300.0, 600.0, 100.0, 300.0, 0, 0),
            DailyTrendRow(date(2026, 8, 4), 1, 0, 1, -50.0, 550.0, 0.0, -50.0, 0, 0),
            DailyTrendRow(date(2026, 8, 5), 1, 1, 0, 75.0, 625.0, 100.0, 75.0, 0, 0),
        ],
        pattern_rows=[],
        trades_by_day={},
    )
    df = daily_trend_dataframe(report)
    assert list(df["Date"]) == [
        date(2026, 7, 30),
        date(2026, 7, 31),
        date(2026, 8, 3),
        date(2026, 8, 4),
        date(2026, 8, 5),
    ]
    assert list(df.sort_values("Date", ascending=True)["Date"]) == list(df["Date"])


@pytest.mark.quick
def test_build_empty_trend_charts_when_no_daily_rows() -> None:
    from app.services.paper_trading_trend import PaperTradingTrendReport
    from ui.paper_trading_trend_display import build_trend_charts, build_win_rate_chart

    report = PaperTradingTrendReport(
        as_of=date(2026, 8, 5),
        window_days=30,
        window_start=date(2026, 7, 6),
        initial_cash=50_000.0,
        cash_balance=50_000.0,
        equity_value=0.0,
        total_value=50_000.0,
        unrealized_pnl=0.0,
        total_realized_pnl=0.0,
        total_return_pct=0.0,
        open_positions=0,
        trading_days=0,
        total_closed_trades=0,
        overall_win_rate_pct=0.0,
        profitable_days=0,
        losing_days=0,
        flat_days=0,
        best_day_pnl=None,
        worst_day_pnl=None,
    )
    cumulative, daily = build_trend_charts(report)
    win = build_win_rate_chart(report)
    assert cumulative.layout.title.text == "Cumulative realized P&L"
    assert daily.layout.title.text == "Daily realized P&L"
    assert win.layout.title.text == "Daily win rate (closed trades)"


@pytest.mark.quick
def test_build_pattern_rows_groups_by_pattern() -> None:
    closed = [
        _trade("AAA", closed_date=date(2026, 7, 28), pnl=400.0, pattern="Hammer"),
        _trade("BBB", closed_date=date(2026, 7, 29), pnl=200.0, pattern="Hammer"),
        _trade("CCC", closed_date=date(2026, 7, 29), pnl=-100.0, pattern="Doji"),
    ]
    patterns = _build_pattern_rows(closed)
    assert patterns[0].pattern_name == "Hammer"
    assert patterns[0].total_pnl == 600.0
    assert patterns[0].trades == 2

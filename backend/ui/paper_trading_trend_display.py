"""Paper trading trend charts and tables for Streamlit."""

from __future__ import annotations

import math

import pandas as pd
import plotly.graph_objects as go

from app.services.paper_trading_trend import PaperTradingTrendReport


def _format_inr(value: float | None) -> str:
    if value is None or not math.isfinite(float(value)):
        return "—"
    return f"₹{value:,.2f}"


def _format_pct(value: float | None) -> str:
    if value is None or not math.isfinite(float(value)):
        return "—"
    return f"{value:+.2f}%"


def daily_trend_dataframe(report: PaperTradingTrendReport) -> pd.DataFrame:
    rows = []
    for day in report.daily_rows:
        rows.append(
            {
                "Date": day.trade_date,
                "Trades closed": day.trades_closed,
                "Wins": day.wins,
                "Losses": day.losses,
                "Day P&L": day.day_pnl,
                "Cumulative P&L": day.cumulative_pnl,
                "Win rate": day.win_rate_pct,
                "Avg P&L / trade": day.avg_pnl_per_trade,
                "Target hits": day.target_hits,
                "Stop hits": day.stop_hits,
            }
        )
    return pd.DataFrame(rows)


def daily_trend_column_config() -> dict[str, object]:
    import streamlit as st

    return {
        "Date": st.column_config.DateColumn(format="DD MMM YYYY"),
        "Day P&L": st.column_config.NumberColumn(format="₹%.2f"),
        "Cumulative P&L": st.column_config.NumberColumn(format="₹%.2f"),
        "Win rate": st.column_config.NumberColumn(format="%.1f%%"),
        "Avg P&L / trade": st.column_config.NumberColumn(format="₹%.2f"),
    }


def pattern_trend_dataframe(report: PaperTradingTrendReport) -> pd.DataFrame:
    rows = []
    for row in report.pattern_rows:
        rows.append(
            {
                "Pattern": row.pattern_name,
                "Trades": row.trades,
                "Wins": row.wins,
                "Win rate": f"{row.win_rate_pct:.1f}%",
                "Total P&L": _format_inr(row.total_pnl),
                "Avg P&L": _format_inr(row.avg_pnl),
            }
        )
    return pd.DataFrame(rows)


def closed_trades_dataframe(trades: list) -> pd.DataFrame:
    rows = []
    for trade in trades:
        rows.append(
            {
                "Stock": trade.symbol,
                "Pattern": trade.pattern_name,
                "Status": trade.status,
                "Shares": trade.shares,
                "Entry": _format_inr(trade.entry_price),
                "Exit": _format_inr(trade.exit_price),
                "Return": _format_pct(trade.return_pct),
                "P&L": _format_inr(trade.realized_pnl),
                "Source": trade.source.title(),
            }
        )
    return pd.DataFrame(rows)


def build_trend_charts(report: PaperTradingTrendReport) -> tuple[go.Figure, go.Figure]:
    if not report.daily_rows:
        return _empty_trend_chart(
            "Cumulative realized P&L",
            "P&L (₹)",
            height=360,
        ), _empty_trend_chart(
            "Daily realized P&L",
            "P&L (₹)",
            height=360,
        )

    dates = [d.trade_date.strftime("%d %b") for d in report.daily_rows]
    day_pnl = [d.day_pnl for d in report.daily_rows]
    cumulative = [d.cumulative_pnl for d in report.daily_rows]
    colors = ["#16a34a" if p >= 0 else "#dc2626" for p in day_pnl]

    cumulative_fig = go.Figure()
    cumulative_fig.add_trace(
        go.Scatter(
            x=dates,
            y=cumulative,
            mode="lines+markers",
            name="Cumulative P&L",
            line=dict(color="#2563eb", width=3),
            fill="tozeroy",
            fillcolor="rgba(37, 99, 235, 0.08)",
        )
    )
    cumulative_fig.add_hline(y=0, line_dash="dot", line_color="#94a3b8")
    cumulative_fig.update_layout(
        title="Cumulative realized P&L",
        xaxis_title="Trading day",
        yaxis_title="P&L (₹)",
        height=360,
        margin=dict(l=40, r=20, t=50, b=40),
        showlegend=False,
    )

    daily_fig = go.Figure()
    daily_fig.add_trace(
        go.Bar(
            x=dates,
            y=day_pnl,
            marker_color=colors,
            name="Day P&L",
        )
    )
    daily_fig.add_hline(y=0, line_dash="dot", line_color="#94a3b8")
    daily_fig.update_layout(
        title="Daily realized P&L",
        xaxis_title="Trading day",
        yaxis_title="P&L (₹)",
        height=360,
        margin=dict(l=40, r=20, t=50, b=40),
        showlegend=False,
    )

    return cumulative_fig, daily_fig


def build_win_rate_chart(report: PaperTradingTrendReport) -> go.Figure:
    if not report.daily_rows:
        return _empty_trend_chart(
            "Daily win rate (closed trades)",
            "Win rate (%)",
            height=320,
            y_range=(0, 100),
        )

    dates = [d.trade_date.strftime("%d %b") for d in report.daily_rows]
    win_rates = [d.win_rate_pct for d in report.daily_rows]

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=dates,
            y=win_rates,
            marker_color="#7c3aed",
            name="Win rate",
        )
    )
    fig.update_layout(
        title="Daily win rate (closed trades)",
        xaxis_title="Trading day",
        yaxis_title="Win rate (%)",
        yaxis=dict(range=[0, 100]),
        height=320,
        margin=dict(l=40, r=20, t=50, b=40),
        showlegend=False,
    )
    return fig


def _empty_trend_chart(
    title: str,
    yaxis_title: str,
    *,
    height: int = 360,
    y_range: tuple[int, int] | None = None,
) -> go.Figure:
    fig = go.Figure()
    fig.add_hline(y=0, line_dash="dot", line_color="#94a3b8")
    layout: dict[str, object] = {
        "title": title,
        "xaxis_title": "Trading day",
        "yaxis_title": yaxis_title,
        "height": height,
        "margin": dict(l=40, r=20, t=50, b=40),
        "showlegend": False,
        "annotations": [
            dict(
                text="No closed trades in this window yet",
                xref="paper",
                yref="paper",
                x=0.5,
                y=0.5,
                showarrow=False,
                font=dict(size=14, color="#94a3b8"),
            )
        ],
    }
    if y_range is not None:
        layout["yaxis"] = dict(range=list(y_range))
    fig.update_layout(**layout)
    return fig


def _empty_trend_chart(
    title: str,
    yaxis_title: str,
    *,
    height: int = 360,
    y_range: tuple[int, int] | None = None,
) -> go.Figure:
    fig = go.Figure()
    fig.add_hline(y=0, line_dash="dot", line_color="#94a3b8")
    layout: dict[str, object] = {
        "title": title,
        "xaxis_title": "Trading day",
        "yaxis_title": yaxis_title,
        "height": height,
        "margin": dict(l=40, r=20, t=50, b=40),
        "showlegend": False,
        "annotations": [
            dict(
                text="No closed trades in this window yet",
                xref="paper",
                yref="paper",
                x=0.5,
                y=0.5,
                showarrow=False,
                font=dict(size=14, color="#94a3b8"),
            )
        ],
    }
    if y_range is not None:
        layout["yaxis"] = dict(range=list(y_range))
    fig.update_layout(**layout)
    return fig

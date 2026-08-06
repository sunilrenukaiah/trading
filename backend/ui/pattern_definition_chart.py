"""Annotated candlestick charts for pattern definition examples."""

from __future__ import annotations

from datetime import date

import pandas as pd
import plotly.graph_objects as go

from ui.recommendation_chart import OVAL_PATTERNS, pattern_candle_span


def build_pattern_definition_chart(
    *,
    pattern_id: str,
    pattern_name: str,
    signal: str,
    candles: pd.DataFrame,
    highlight_bars: int,
) -> go.Figure:
    """Candlestick chart highlighting the pattern-forming bars."""
    if candles.empty or len(candles) < 3:
        fig = go.Figure()
        fig.update_layout(title=f"{pattern_name} — insufficient example data", height=400)
        return fig

    dates = [d.date() if hasattr(d, "date") else d for d in candles["trade_date"]]
    span = min(highlight_bars or pattern_candle_span(pattern_id), len(candles))
    pat_start = len(candles) - span
    pat_end = len(candles) - 1

    y_pad = float(candles["high"].max() - candles["low"].min())
    y_pad = y_pad * 0.1 if y_pad > 0 else 1.0
    y_min = float(candles["low"].min()) - y_pad
    y_max = float(candles["high"].max()) + y_pad * 1.8

    fig = go.Figure(
        data=[
            go.Candlestick(
                x=dates,
                open=candles["open"],
                high=candles["high"],
                low=candles["low"],
                close=candles["close"],
                increasing_line_color="#26a69a",
                decreasing_line_color="#ef5350",
                name="Example OHLCV",
            )
        ]
    )

    pat_low = float(candles["low"].iloc[pat_start: pat_end + 1].min())
    pat_high = float(candles["high"].iloc[pat_start: pat_end + 1].max())
    x0, x1 = dates[pat_start], dates[pat_end]
    cx = dates[pat_start + (pat_end - pat_start) // 2]

    fill = "rgba(38, 166, 154, 0.15)" if signal.upper() != "BEARISH" else "rgba(239, 83, 80, 0.15)"
    if pattern_id in OVAL_PATTERNS:
        fig.add_shape(
            type="circle",
            xref="x",
            yref="y",
            x0=dates[max(pat_start - 1, 0)],
            x1=dates[min(pat_end + 1, len(dates) - 1)],
            y0=pat_low - y_pad * 0.2,
            y1=pat_high + y_pad * 0.2,
            line=dict(color="#37474f", width=2),
            fillcolor=fill,
        )
    else:
        fig.add_shape(
            type="rect",
            xref="x",
            yref="y",
            x0=x0,
            x1=x1,
            y0=pat_low - y_pad * 0.15,
            y1=pat_high + y_pad * 0.15,
            line=dict(color="#37474f", width=2),
            fillcolor=fill,
        )

    arrow_color = "#2e7d32" if signal.upper() == "BULLISH" else "#c62828"
    if signal.upper() in {"BULLISH", "BEARISH"}:
        last_close = float(candles["close"].iloc[-1])
        target = last_close * (1.03 if signal.upper() == "BULLISH" else 0.97)
        fig.add_trace(
            go.Scatter(
                x=[dates[pat_end], dates[pat_end]],
                y=[last_close, target],
                mode="lines+markers",
                line=dict(color=arrow_color, width=2, dash="dash"),
                marker=dict(size=[8, 10], color=arrow_color),
                name="Typical follow-through",
            )
        )
        fig.add_annotation(
            x=dates[pat_end],
            y=(last_close + target) / 2,
            text=f"{signal.title()} bias",
            showarrow=True,
            arrowhead=2,
            ax=40,
            ay=-30 if signal.upper() == "BULLISH" else 30,
            font=dict(size=12, color=arrow_color),
        )

    fig.add_annotation(
        x=cx,
        y=pat_low - y_pad * 0.8,
        text=pattern_name,
        showarrow=True,
        arrowhead=2,
        ax=0,
        ay=-35,
        font=dict(size=13, color="#111111"),
    )

    fig.update_layout(
        title=dict(
            text=f"{pattern_name} — illustrative example",
            x=0.01,
            xanchor="left",
        ),
        height=420,
        margin=dict(l=0, r=40, t=60, b=30),
        xaxis_rangeslider_visible=False,
        template="plotly_white",
        yaxis=dict(title="Price (INR)", range=[y_min, y_max]),
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    )
    return fig

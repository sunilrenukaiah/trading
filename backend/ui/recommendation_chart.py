"""Annotated candlestick charts explaining recommendation signals."""

from __future__ import annotations

from datetime import date
from typing import Sequence

import plotly.graph_objects as go

from app.services.recommendation_engine import StockRecommendation

# Pattern id -> number of recent candles forming the signal (at end of lookback window).
PATTERN_CANDLE_SPAN: dict[str, int] = {
    "cs_three_white_soldiers": 3,
    "cs_three_black_crows": 3,
    "cs_morning_star": 3,
    "cs_evening_star": 3,
    "cs_bullish_harami": 2,
    "cs_bearish_harami": 2,
    "cs_piercing_line": 2,
    "cs_dark_cloud_cover": 2,
    "cs_three_inside_up": 3,
    "cs_three_inside_down": 3,
    "cs_three_outside_up": 3,
    "cs_three_outside_down": 3,
    "cs_bullish_kicker": 2,
    "cs_bearish_kicker": 2,
    "cs_tweezer_bottom": 2,
    "cs_tweezer_top": 2,
    "cs_bullish_abandoned_baby": 3,
    "cs_bearish_abandoned_baby": 3,
    "cs_bullish_separating_lines": 2,
    "cs_meeting_lines": 2,
    "cs_bullish_three_line_strike": 4,
    "cs_bearish_three_line_strike": 4,
    "cs_ladder_bottom": 5,
    "cs_rising_three_methods": 5,
    "cs_bullish_mat_hold": 5,
    "cs_bearish_mat_hold": 5,
    "cs_concealing_baby_swallow": 4,
    "cs_upside_gap_two_crows": 3,
    "p7_engulfing": 2,
    "p10_doji": 1,
    "p11_hammer": 1,
    "cs_inverted_hammer": 1,
    "cs_shooting_star": 1,
    "cs_hanging_man": 1,
    "cs_dragonfly_doji": 1,
    "cs_bullish_belt_hold": 1,
    "cs_bearish_belt_hold": 1,
    "cs_bearish_doji_star": 2,
}

OVAL_PATTERNS = frozenset(
    {
        "cs_three_white_soldiers",
        "cs_three_black_crows",
        "cs_morning_star",
        "cs_ladder_bottom",
    }
)


def pattern_candle_span(pattern_id: str) -> int:
    return PATTERN_CANDLE_SPAN.get(pattern_id, 3)


class _CandleRow:
    trade_date: date
    open: float
    high: float
    low: float
    close: float

    def __init__(self, trade_date, open_, high, low, close):
        self.trade_date = trade_date
        self.open = float(open_)
        self.high = float(high)
        self.low = float(low)
        self.close = float(close)


def _normalize_candles(candles: Sequence) -> list[_CandleRow]:
    rows: list[_CandleRow] = []
    for c in candles:
        td = c.trade_date
        if hasattr(td, "date"):
            td = td.date()
        rows.append(_CandleRow(td, c.open, c.high, c.low, c.close))
    return rows


def build_recommendation_chart(
    rec: StockRecommendation,
    candles: Sequence,
    *,
    lookback_days: int = 20,
) -> go.Figure:
    """Candlestick chart with trend context, pattern highlight, and trade levels."""
    rows = _normalize_candles(candles)
    if len(rows) < 10:
        fig = go.Figure()
        fig.update_layout(title="Not enough candle data for chart", height=420)
        return fig

    # Show a window around the signal (lookback + context).
    window = min(len(rows), max(lookback_days + 12, 30))
    view = rows[-window:]
    dates = [r.trade_date for r in view]
    span = min(pattern_candle_span(rec.pattern_id), len(view))
    pat_start = len(view) - span
    pat_end = len(view) - 1

    y_pad = max(r.high for r in view) - min(r.low for r in view)
    y_pad = y_pad * 0.08 if y_pad > 0 else 1.0
    y_min = min(r.low for r in view) - y_pad
    y_max = max(r.high for r in view) + y_pad * 2.5

    fig = go.Figure(
        data=[
            go.Candlestick(
                x=dates,
                open=[r.open for r in view],
                high=[r.high for r in view],
                low=[r.low for r in view],
                close=[r.close for r in view],
                increasing_line_color="#26a69a",
                decreasing_line_color="#ef5350",
                name=rec.symbol,
            )
        ]
    )

    pat_low = min(view[i].low for i in range(pat_start, pat_end + 1))
    pat_high = max(view[i].high for i in range(pat_start, pat_end + 1))
    x0, x1 = dates[pat_start], dates[pat_end]
    cx = dates[pat_start + (pat_end - pat_start) // 2]

    if rec.pattern_id in OVAL_PATTERNS:
        fig.add_shape(
            type="circle",
            xref="x",
            yref="y",
            x0=dates[max(pat_start - 1, 0)],
            x1=dates[min(pat_end + 1, len(dates) - 1)],
            y0=pat_low - y_pad * 0.3,
            y1=pat_high + y_pad * 0.3,
            line=dict(color="#111111", width=2),
            fillcolor="rgba(38, 166, 154, 0.12)",
        )
    else:
        fig.add_shape(
            type="rect",
            xref="x",
            yref="y",
            x0=x0,
            x1=x1,
            y0=pat_low - y_pad * 0.25,
            y1=pat_high + y_pad * 0.25,
            line=dict(color="#111111", width=2),
            fillcolor="rgba(38, 166, 154, 0.15)",
        )

    # Prior swing context — downtrend line before the pattern zone.
    if pat_start >= 4:
        pre = view[max(0, pat_start - 10) : pat_start]
        if len(pre) >= 3 and pre[0].close > pre[-1].close:
            fig.add_trace(
                go.Scatter(
                    x=[pre[0].trade_date, pre[-1].trade_date],
                    y=[pre[0].high, pre[-1].low],
                    mode="lines+markers",
                    line=dict(color="#546e7a", width=2, dash="dot"),
                    marker=dict(size=6, color="#546e7a"),
                    name="Downtrend",
                )
            )
            fig.add_annotation(
                x=pre[len(pre) // 2].trade_date,
                y=pre[len(pre) // 2].high + y_pad,
                text="Downtrend",
                showarrow=True,
                arrowhead=2,
                ax=0,
                ay=35,
                font=dict(size=12, color="#37474f"),
            )

    # Expected bullish move from pattern completion.
    fig.add_trace(
        go.Scatter(
            x=[dates[pat_end], dates[pat_end]],
            y=[view[pat_end].close, rec.actual_sell_price],
            mode="lines+markers",
            line=dict(color="#2e7d32", width=2, dash="dash"),
            marker=dict(size=[8, 10], color="#2e7d32"),
            name="Expected move",
        )
    )
    fig.add_annotation(
        x=dates[pat_end],
        y=(view[pat_end].close + rec.actual_sell_price) / 2,
        text="Uptrend expected",
        showarrow=True,
        arrowhead=2,
        ax=45,
        ay=-35,
        font=dict(size=12, color="#2e7d32"),
    )

    fig.add_annotation(
        x=cx,
        y=pat_low - y_pad * 1.2,
        text=rec.pattern_name,
        showarrow=True,
        arrowhead=2,
        ax=0,
        ay=-40,
        font=dict(size=13, color="#111111"),
    )

    for y_val, label, color, dash in (
        (rec.stop_loss, "Stop loss", "#c62828", "dash"),
        (rec.resistance, "Resistance", "#ef6c00", "dot"),
        (rec.sell_price, "Model target", "#1565c0", "dashdot"),
        (rec.actual_sell_price, "Actual sell (conservative)", "#2e7d32", "solid"),
    ):
        fig.add_hline(
            y=y_val,
            line=dict(color=color, width=1.5, dash=dash),
            annotation_text=label,
            annotation_position="right",
            annotation_font_size=10,
        )

    subtitle = (
        f"BUY signal · {rec.pattern_name} · 15d hit {rec.pattern_hit_rate_30d:.0f}% · "
        f"confidence {rec.confidence_score:.0f}"
    )
    if rec.supporting_patterns:
        subtitle += f" · Also: {', '.join(rec.supporting_patterns[:2])}"

    fig.update_layout(
        title=dict(
            text=f"{rec.symbol} — Recommendation chart<br><sup>{subtitle}</sup>",
            x=0.01,
            xanchor="left",
        ),
        height=520,
        margin=dict(l=0, r=80, t=80, b=40),
        xaxis_rangeslider_visible=False,
        template="plotly_white",
        yaxis=dict(title="Price (INR)", range=[y_min, y_max]),
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    )
    return fig

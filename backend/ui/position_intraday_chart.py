"""Intraday position chart with pattern target markers."""

from __future__ import annotations

import importlib
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ui.async_runner import run_async
from ui.helpers import _load_position_chart_context, format_inr

# Local default avoids ImportError when Streamlit caches a pre-refresh intraday_chart module.
DEFAULT_INTERVAL = "15m"
INTERVAL_LABELS = ("5m", "10m", "15m", "30m", "1h")
_DIALOG_SESSION_KEY = "_pos_chart_dialog"
_DIALOG_OPEN_KEY = "_pos_chart_dialog_open"
_CONTEXT_SESSION_PREFIX = "_pos_chart_ctx_"


def request_position_chart_dialog(
    symbol: str,
    *,
    live_price: float | None = None,
    mark_price: float | None = None,
) -> None:
    """Open the chart dialog for a symbol (call render in the same script run)."""
    previous = st.session_state.get(_DIALOG_SESSION_KEY)
    if previous and previous.get("symbol") != symbol:
        st.session_state.pop(f"{_CONTEXT_SESSION_PREFIX}{previous['symbol']}", None)
    st.session_state[_DIALOG_SESSION_KEY] = {
        "symbol": symbol,
        "live_price": live_price,
        "mark_price": mark_price,
    }
    st.session_state[_DIALOG_OPEN_KEY] = True


def _on_position_chart_dialog_dismiss() -> None:
    """Clear persisted dialog state when the user closes the modal (X or backdrop)."""
    spec = st.session_state.get(_DIALOG_SESSION_KEY)
    if spec:
        _close_position_chart_dialog(spec["symbol"])
    else:
        st.session_state.pop(_DIALOG_OPEN_KEY, None)


def render_position_chart_dialog_if_open() -> None:
    if not st.session_state.get(_DIALOG_OPEN_KEY):
        return
    spec = st.session_state.get(_DIALOG_SESSION_KEY)
    if spec:
        show_position_chart_dialog(**spec)


def _close_position_chart_dialog(symbol: str) -> None:
    st.session_state.pop(_DIALOG_SESSION_KEY, None)
    st.session_state.pop(_DIALOG_OPEN_KEY, None)
    st.session_state.pop(f"{_CONTEXT_SESSION_PREFIX}{symbol}", None)
    st.session_state.pop(f"pos_chart_interval_{symbol}", None)


def _load_cached_position_chart_context(
    symbol: str,
    *,
    live_price: float | None,
    mark_price: float | None,
) -> Any:
    cache_key = f"{_CONTEXT_SESSION_PREFIX}{symbol}"
    if cache_key not in st.session_state:
        st.session_state[cache_key] = run_async(
            _load_position_chart_context(
                symbol,
                live_price=live_price,
                mark_price=mark_price,
            )
        )
    return st.session_state[cache_key]


def _intraday_chart():
    from app.services import intraday_chart

    if not hasattr(intraday_chart, "DEFAULT_INTERVAL"):
        return importlib.reload(intraday_chart)
    return intraday_chart


def _interval_options() -> dict[str, int]:
    mod = _intraday_chart()
    return dict(getattr(mod, "INTERVAL_OPTIONS", {"5m": 5, "10m": 10, "15m": 15, "30m": 30, "1h": 60}))


def _resample_bars(bars, minutes: int):
    return _intraday_chart().resample_intraday_bars(bars, minutes)


def _position_context_type():
    return _intraday_chart().PositionIntradayContext


def position_summary_dataframe(ctx) -> pd.DataFrame:
    rows = [
        ("Prev close", format_inr(ctx.prev_close)),
        ("Today's open", format_inr(ctx.today_open)),
        ("Today's high", format_inr(ctx.today_high)),
        ("Today's low", format_inr(ctx.today_low)),
        ("Current price", format_inr(ctx.current_price)),
        ("Target (bracket)", format_inr(ctx.target_price)),
        ("Stop loss", format_inr(ctx.stop_loss_price)),
    ]
    if ctx.pattern_name:
        rows.insert(0, ("Pattern", ctx.pattern_name))
    return pd.DataFrame({"Metric": [r[0] for r in rows], "Value": [r[1] for r in rows]})


def pattern_targets_dataframe(ctx) -> pd.DataFrame:
    """Pattern-derived price levels shown as chart markers."""
    rows: list[dict[str, str]] = []
    if ctx.model_target_price is not None:
        rows.append({"Level": "Model target", "Price": format_inr(ctx.model_target_price)})
    if ctx.target_price is not None:
        rows.append({"Level": "Actual sell target", "Price": format_inr(ctx.target_price)})
    if ctx.resistance is not None:
        rows.append({"Level": "Resistance", "Price": format_inr(ctx.resistance)})
    if ctx.stop_loss_price is not None:
        rows.append({"Level": "Stop loss", "Price": format_inr(ctx.stop_loss_price)})
    if ctx.entry_price is not None:
        rows.append({"Level": "Entry", "Price": format_inr(ctx.entry_price)})
    if not rows:
        return pd.DataFrame(columns=["Level", "Price"])
    return pd.DataFrame(rows)


def build_position_intraday_chart(
    ctx,
    *,
    bars=None,
    interval_label: str = DEFAULT_INTERVAL,
) -> go.Figure:
    display_bars = bars if bars is not None else ctx.bars
    fig = go.Figure()

    if not display_bars:
        fig.update_layout(
            title=f"{ctx.symbol} — no intraday data available",
            height=420,
            template="plotly_white",
        )
        return fig

    times = [b.timestamp for b in display_bars]
    opens = [b.open for b in display_bars]
    highs = [b.high for b in display_bars]
    lows = [b.low for b in display_bars]
    closes = [b.close for b in display_bars]

    fig.add_trace(
        go.Candlestick(
            x=times,
            open=opens,
            high=highs,
            low=lows,
            close=closes,
            increasing_line_color="#26a69a",
            decreasing_line_color="#ef5350",
            increasing_fillcolor="#26a69a",
            decreasing_fillcolor="#ef5350",
            name=ctx.symbol,
            showlegend=False,
            hovertext=[
                f"O {o:,.2f}  H {h:,.2f}<br>L {l:,.2f}  C {c:,.2f}"
                for o, h, l, c in zip(opens, highs, lows, closes)
            ],
            hoverinfo="text+x",
        )
    )

    if ctx.prev_close is not None:
        fig.add_hline(
            y=ctx.prev_close,
            line=dict(color="#78909c", width=1, dash="dot"),
        )

    if ctx.today_open is not None:
        fig.add_hline(
            y=ctx.today_open,
            line=dict(color="#5c6bc0", width=1, dash="dash"),
        )

    reference_specs: list[tuple[float, str, str, str]] = []
    if ctx.prev_close is not None:
        reference_specs.append((ctx.prev_close, "Prev close", "#78909c", "line-ns"))
    if ctx.today_open is not None:
        reference_specs.append((ctx.today_open, "Open", "#5c6bc0", "line-ns"))

    level_specs: list[tuple[float, str, str, str]] = []
    if ctx.stop_loss_price is not None:
        level_specs.append((ctx.stop_loss_price, "Stop loss", "#c62828", "triangle-down"))
    if ctx.resistance is not None:
        level_specs.append((ctx.resistance, "Resistance", "#ef6c00", "diamond"))
    if ctx.model_target_price is not None:
        level_specs.append((ctx.model_target_price, "Model target", "#1565c0", "triangle-up"))
    if ctx.target_price is not None:
        level_specs.append((ctx.target_price, "Actual target", "#2e7d32", "star"))
    if ctx.entry_price is not None:
        level_specs.append((ctx.entry_price, "Entry", "#6a1b9a", "circle"))

    all_levels = reference_specs + level_specs
    y_vals = highs + lows + closes + [lvl for lvl, _, _, _ in all_levels]
    y_pad = (max(y_vals) - min(y_vals)) * 0.12 if y_vals else 1.0
    y_min = min(y_vals) - y_pad if y_vals else 0
    y_max = max(y_vals) + y_pad if y_vals else 1

    marker_time = times[-1]
    for y_val, label, color, _marker_symbol in level_specs:
        fig.add_hline(
            y=y_val,
            line=dict(color=color, width=1.5, dash="dashdot"),
        )
    for y_val, label, color, marker_symbol in all_levels:
        fig.add_trace(
            go.Scatter(
                x=[marker_time],
                y=[y_val],
                mode="markers",
                marker=dict(
                    symbol=marker_symbol,
                    size=11 if marker_symbol == "line-ns" else 13,
                    color=color,
                    line=dict(width=1, color="#fff"),
                ),
                name=label,
                hovertemplate=f"{label}<br>₹%{{y:,.2f}}<extra></extra>",
            )
        )

    subtitle = f"{interval_label} candles"
    if ctx.data_source == "nse_quote":
        subtitle += " · open-to-now estimate (no minute bars)"

    fig.update_layout(
        title=dict(
            text=f"{ctx.symbol} — intraday<br><sup>{subtitle}</sup>",
            x=0.01,
            xanchor="left",
        ),
        height=500,
        margin=dict(l=48, r=175, t=72, b=48),
        template="plotly_white",
        xaxis=dict(title="Time (IST)", tickformat="%H:%M", rangeslider_visible=False),
        yaxis=dict(title="Price (INR)", range=[y_min, y_max]),
        legend=dict(
            orientation="v",
            yanchor="top",
            y=0.98,
            x=1.01,
            xanchor="left",
            bgcolor="rgba(255,255,255,0.9)",
            bordercolor="rgba(0,0,0,0.12)",
            borderwidth=1,
            font=dict(size=11),
            itemsizing="constant",
            tracegroupgap=4,
        ),
        hovermode="x unified",
    )
    return fig


@st.dialog("Position intraday chart", width="large", on_dismiss=_on_position_chart_dialog_dismiss)
def show_position_chart_dialog(
    symbol: str,
    live_price: float | None = None,
    mark_price: float | None = None,
) -> None:
    header_col, close_col = st.columns([5, 1])
    with close_col:
        if st.button("Close", key=f"pos_chart_close_{symbol}"):
            _close_position_chart_dialog(symbol)
            st.rerun()

    cache_key = f"{_CONTEXT_SESSION_PREFIX}{symbol}"
    if cache_key not in st.session_state:
        with st.spinner(f"Loading {symbol} intraday chart…"):
            try:
                ctx = _load_cached_position_chart_context(
                    symbol,
                    live_price=live_price,
                    mark_price=mark_price,
                )
            except Exception as exc:
                st.error(f"Could not load chart for {symbol}: {exc}")
                return
    else:
        try:
            ctx = _load_cached_position_chart_context(
                symbol,
                live_price=live_price,
                mark_price=mark_price,
            )
        except Exception as exc:
            st.error(f"Could not load chart for {symbol}: {exc}")
            return

    if not ctx.pattern_name:
        st.caption(
            "No recommendation pattern on file — likely a manual buy or a pick outside the last saved analysis."
        )
    else:
        with header_col:
            st.caption(f"Pattern: **{ctx.pattern_name}**")

    default_idx = INTERVAL_LABELS.index(DEFAULT_INTERVAL)
    interval_options = _interval_options()
    interval_label = st.selectbox(
        "Candle interval",
        INTERVAL_LABELS,
        index=default_idx,
        key=f"pos_chart_interval_{symbol}",
        help="Resampled from 5m data — switching interval does not refetch quotes.",
    )
    display_bars = _resample_bars(ctx.bars, interval_options[interval_label])

    st.plotly_chart(
        build_position_intraday_chart(ctx, bars=display_bars, interval_label=interval_label),
        use_container_width=True,
        key=f"pos_intraday_plot_{symbol}_{interval_label}",
    )

    left, right = st.columns(2)
    with left:
        st.markdown("**Day summary**")
        st.table(position_summary_dataframe(ctx))
    with right:
        st.markdown("**Pattern price levels**")
        targets_df = pattern_targets_dataframe(ctx)
        if targets_df.empty:
            st.caption("No bracket or recommendation levels on file for this symbol.")
        else:
            st.table(targets_df)

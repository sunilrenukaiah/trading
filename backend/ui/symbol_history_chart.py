"""Historical OHLCV chart dialog for a selected symbol."""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from ui.async_runner import run_async
from ui.helpers import _candles

_DIALOG_SESSION_KEY = "_symbol_history_dialog"
_DIALOG_OPEN_KEY = "_symbol_history_dialog_open"
_CANDLES_CACHE_PREFIX = "_symbol_history_candles_"


def request_symbol_history_chart_dialog(symbol: str, *, days: int = 30) -> None:
    """Open the history chart dialog (call render in the same script run)."""
    previous = st.session_state.get(_DIALOG_SESSION_KEY)
    if previous and previous.get("symbol") != symbol:
        _clear_candles_cache(previous["symbol"])
    st.session_state[_DIALOG_SESSION_KEY] = {
        "symbol": symbol,
        "days": int(days),
    }
    st.session_state[_DIALOG_OPEN_KEY] = True


def _on_symbol_history_chart_dialog_dismiss() -> None:
    spec = st.session_state.get(_DIALOG_SESSION_KEY)
    if spec:
        _close_symbol_history_chart_dialog(spec["symbol"])
    else:
        st.session_state.pop(_DIALOG_OPEN_KEY, None)


def render_symbol_history_chart_dialog_if_open() -> None:
    if not st.session_state.get(_DIALOG_OPEN_KEY):
        return
    spec = st.session_state.get(_DIALOG_SESSION_KEY)
    if spec:
        show_symbol_history_chart_dialog(**spec)


def _cache_key(symbol: str, days: int) -> str:
    return f"{_CANDLES_CACHE_PREFIX}{symbol}_{days}"


def _clear_candles_cache(symbol: str) -> None:
    prefix = f"{_CANDLES_CACHE_PREFIX}{symbol}_"
    for key in list(st.session_state.keys()):
        if isinstance(key, str) and key.startswith(prefix):
            st.session_state.pop(key, None)


def _close_symbol_history_chart_dialog(symbol: str) -> None:
    st.session_state.pop(_DIALOG_SESSION_KEY, None)
    st.session_state.pop(_DIALOG_OPEN_KEY, None)
    _clear_candles_cache(symbol)
    st.session_state.pop(f"symbol_hist_days_{symbol}", None)


def _load_cached_candles(symbol: str, days: int):
    key = _cache_key(symbol, days)
    if key not in st.session_state:
        st.session_state[key] = run_async(_candles(symbol, days))
    return st.session_state[key]


def build_symbol_history_chart(symbol: str, candles, *, days: int) -> go.Figure:
    fig = go.Figure()
    if not candles:
        fig.update_layout(
            title=f"{symbol} — no candle data",
            height=420,
            template="plotly_white",
        )
        return fig

    fig.add_trace(
        go.Candlestick(
            x=[c.trade_date for c in candles],
            open=[float(c.open) for c in candles],
            high=[float(c.high) for c in candles],
            low=[float(c.low) for c in candles],
            close=[float(c.close) for c in candles],
            increasing_line_color="#26a69a",
            decreasing_line_color="#ef5350",
            name=symbol,
            showlegend=False,
        )
    )
    fig.update_layout(
        title=dict(
            text=f"{symbol} — {days}-day chart",
            x=0.01,
            xanchor="left",
        ),
        height=500,
        margin=dict(l=48, r=24, t=56, b=40),
        xaxis_rangeslider_visible=False,
        template="plotly_white",
        yaxis_title="Price (INR)",
    )
    return fig


@st.dialog("Symbol chart", width="large", on_dismiss=_on_symbol_history_chart_dialog_dismiss)
def show_symbol_history_chart_dialog(symbol: str, days: int = 30) -> None:
    _, close_col = st.columns([5, 1])
    with close_col:
        if st.button("Close", key=f"symbol_hist_close_{symbol}"):
            _close_symbol_history_chart_dialog(symbol)
            st.rerun()

    chart_days = st.slider(
        "Days",
        min_value=7,
        max_value=90,
        value=int(days),
        key=f"symbol_hist_days_{symbol}",
    )

    cache_key = _cache_key(symbol, chart_days)
    if cache_key not in st.session_state:
        with st.spinner(f"Loading {symbol} chart…"):
            candles = _load_cached_candles(symbol, chart_days)
    else:
        candles = _load_cached_candles(symbol, chart_days)

    if not candles:
        st.info("No candle data yet. Click **Refresh market data** in the sidebar.")
        return

    st.plotly_chart(
        build_symbol_history_chart(symbol, candles, days=chart_days),
        use_container_width=True,
        key=f"symbol_hist_plot_{symbol}_{chart_days}",
    )

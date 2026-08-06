"""NIFTY Paper Trading — Streamlit UI."""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

# Streamlit Cloud runs with repo root as cwd; local run_app uses backend/.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ui.streamlit_imports import ensure_fresh_ui_modules

if "_ui_modules_fresh_v8" not in st.session_state:
    ensure_fresh_ui_modules()
    st.session_state["_ui_modules_fresh_v8"] = True

from ui.instance_guard import assert_instance_isolation

assert_instance_isolation()

from app.schemas import OrderSide, OrderType, PlaceOrderRequest
from app.services.simulation_cache import load_cached_simulation
from ui.async_runner import run_async
from ui.backtest_display import (
    build_bearish_mismatch_summary,
    build_bullish_summary_matrix,
    build_validation_scorecard,
    day_details_dataframe,
    pattern_detail_from_result,
    prediction_context,
    style_bullish_summary,
)
from ui.helpers import (
    _backtest_pattern_detail,
    _candles,
    _cancel_order,
    _cleanup_duplicate_session_orders,
    _list_eod_trade_dates,
    _load_order_bracket_context,
    _load_position_bracket_levels,
    _load_allocation_trade_plan_state,
    _load_midday_place_state,
    _load_trading_page_data,
    _market_summary,
    _market_data_stats,
    _load_paper_trading_trend,
    _realized_pnl_after_tax_summary,
    _orders,
    _place_allocation_buy,
    _place_midday_allocation_buy,
    _place_all_midday_orders,
    _midday_budget_context,
    _place_all_allocation_orders,
    _place_order,
    _positions,
    _process_trade_plans_live,
    _refresh_live_trading,
    _recommended_symbols_for_session,
    _reconcile_brackets_if_needed,
    _recommendation_bracket_symbols,
    _run_eod_trade_analysis,
    _trades,
    ensure_ready,
    format_inr,
    format_ist_datetime,
    format_pct,
    list_registered_patterns,
)
from ui.live_quote_poller import LIVE_POLL_INTERVAL_SEC
from ui.eod_analysis_display import (
    better_patterns_dataframe,
    executed_trade_reviews_dataframe,
    missed_profitable_trades_dataframe,
    missed_target_dataframe,
    trade_analysis_dataframe,
)
from ui.pattern_definitions_display import render_pattern_definitions_page
from ui.paper_trading_trend_display import (
    build_trend_charts,
    build_win_rate_chart,
    closed_trades_dataframe,
    daily_trend_dataframe,
    daily_trend_column_config,
    pattern_trend_dataframe,
)
from ui.job_api import (
    JobKind,
    cancel_running_job,
    is_any_job_running,
    is_kind_running,
    list_jobs,
    run_background_job_watcher,
    start_market_sync_job,
    start_midday_recommendations_job,
    start_recommendations_job,
    start_sim_backtest_job,
    start_today_prediction_job,
    sync_jobs_to_session,
)
from ui.recommendations_display import (
    allocation_dataframe,
    allocation_simulation_dataframe,
    allocation_summary_rows,
    budget_simulation_comparison_dataframe,
    format_sell_target_display,
    patterns_dataframe,
    recommendations_dataframe,
    report_recommendations_dataframe,
)
from app.services.live_quotes import live_quote_ltp
from ui.symbol_history_chart import (
    render_symbol_history_chart_dialog_if_open,
    request_symbol_history_chart_dialog,
)
from ui.position_intraday_chart import (
    render_position_chart_dialog_if_open,
    request_position_chart_dialog,
)
from ui.positions_display import (
    COLUMN_LABELS,
    SORTABLE_COLUMNS,
    build_position_rows,
    current_price_html,
    open_price_html,
    session_high_html,
    sort_position_rows,
    symbol_html,
    target_gap_html,
)
from ui.recommendation_chart import build_recommendation_chart

st.set_page_config(
    page_title="NIFTY Paper Trading",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _position_snapshot(
    position,
    live_quotes: dict,
) -> tuple[float | None, float | None, float | None]:
    """Return (current_price, market_value, unrealized_pnl) using live LTP or EOD mark."""
    live_price = live_quote_ltp(live_quotes, position.symbol)
    mark = float(position.mark_price) if position.mark_price is not None else None
    current = live_price if live_price is not None else mark
    if current is None:
        return None, None, None
    mkt_value = current * position.quantity
    unrealized = (current - float(position.avg_cost)) * position.quantity
    return current, mkt_value, unrealized


def _apply_live_trading_refresh(position_symbols: list[str]) -> tuple[bool, dict[str, int]]:
    """Fetch live quotes, process bracket orders, update session + module cache."""
    from ui.live_quote_poller import publish_refresh_result
    from ui.streamlit_imports import ensure_live_quotes_fresh

    ensure_live_quotes_fresh()
    quotes, stats = run_async(_refresh_live_trading(position_symbols))
    quote_map = dict(quotes) if quotes else {}
    notice = None
    if stats:
        parts = [f"{k.replace('_', ' ')}: {v}" for k, v in stats.items() if v]
        if parts:
            notice = "Bracket orders updated — " + ", ".join(parts)
    publish_refresh_result(quote_map, stats, symbols=position_symbols)
    if quote_map:
        st.session_state["position_live_quotes"] = quote_map
        from app.services.market_calendar import IST

        st.session_state["position_live_quotes_at"] = datetime.now(IST)
    if notice:
        st.session_state["trade_plan_live_notice"] = notice
    return bool(quotes), stats or {}


def _positions_sort_indicator(column: str) -> str:
    if st.session_state.get("pos_sort_column") != column:
        return ""
    return " ↑" if st.session_state.get("pos_sort_asc") else " ↓"


def _positions_sort_click(column: str) -> None:
    if st.session_state.get("pos_sort_column") == column:
        st.session_state["pos_sort_asc"] = not st.session_state.get("pos_sort_asc", False)
    else:
        st.session_state["pos_sort_column"] = column
        st.session_state["pos_sort_asc"] = column == "symbol"
    st.rerun()


def _cached_position_bracket_levels() -> dict[str, tuple[float, float]]:
    """Load bracket levels; reuse cache during hot-reload import races."""
    from ui.streamlit_imports import is_hot_reload_import_error

    cache_key = "_cached_position_bracket_levels"
    try:
        levels = run_async(_load_position_bracket_levels())
        st.session_state[cache_key] = levels
        return levels
    except Exception as exc:
        if is_hot_reload_import_error(exc):
            return st.session_state.get(cache_key, {})
        raise


def _positions_tab_content(*, auto_poll: bool) -> None:
    """Render positions table from cached live quotes (refresh runs in background)."""
    from app.schemas import PositionSource
    from app.services.market_calendar import is_live_quote_session

    render_position_chart_dialog_if_open()

    positions = st.session_state.get("_trading_positions") or []
    live_polling = st.session_state.get("live_polling_enabled", True)

    if is_live_quote_session() and not live_polling:
        st.warning(
            "Live polling is **off** during market hours. Turn it on so bracket trades "
            "can fill entries and exit at target, stop loss, or **3:25 PM IST** only."
        )

    if live_poll_err := st.session_state.get("live_poll_error"):
        st.caption(f"Live quote error: {live_poll_err}")
    elif fetched_at := st.session_state.get("position_live_quotes_at"):
        st.caption(
            f"Prices as of {fetched_at.strftime('%H:%M:%S')} IST · NSE quote API (not stored OHLCV)"
        )
    elif not is_live_quote_session():
        st.caption(
            "Outside session hours — **Open price** shows today's open when synced; "
            "prev close in brackets. Live updates run 9:15 AM–4:30 PM IST."
        )
    elif live_polling and not auto_poll:
        st.caption(
            "Manual mode — click **Fetch live prices** to refresh LTP and process bracket orders."
        )

    if notice := st.session_state.pop("trade_plan_live_notice", None):
        st.success(notice)

    live_quotes: dict = st.session_state.get("position_live_quotes", {})

    if positions:
        if "pos_sort_column" not in st.session_state:
            st.session_state["pos_sort_column"] = "unrealized_pnl"
            st.session_state["pos_sort_asc"] = False

        bracket_levels = _cached_position_bracket_levels()
        table_rows = build_position_rows(
            positions,
            live_quotes,
            bracket_levels,
            snapshot_fn=_position_snapshot,
        )
        table_rows = sort_position_rows(
            table_rows,
            st.session_state["pos_sort_column"],
            ascending=st.session_state.get("pos_sort_asc", False),
        )

        unrealized_by_symbol: list[tuple[str, float]] = []
        for row in table_rows:
            if row.unrealized_pnl is not None:
                unrealized_by_symbol.append((row.symbol, row.unrealized_pnl))

        if unrealized_by_symbol:
            total_unrealized = sum(u for _, u in unrealized_by_symbol)
            in_profit = sum(1 for _, u in unrealized_by_symbol if u > 0)
            in_loss = sum(1 for _, u in unrealized_by_symbol if u < 0)
            n = len(unrealized_by_symbol)
            s1, s2, s3 = st.columns(3)
            s1.metric("Total unrealized P&L", format_inr(total_unrealized))
            s2.metric("Stocks in profit", f"{in_profit} / {n}")
            s3.metric("Stocks in loss", f"{in_loss} / {n}")
            if total_unrealized > 0:
                st.caption(
                    f"Overall **in profit** by {format_inr(total_unrealized)} on open holdings "
                    f"({in_profit} up, {in_loss} down)."
                )
            elif total_unrealized < 0:
                st.caption(
                    f"Overall **in loss** by {format_inr(abs(total_unrealized))} on open holdings "
                    f"({in_profit} up, {in_loss} down)."
                )
            else:
                st.caption(f"Open holdings are **flat** ({in_profit} up, {in_loss} down).")
            st.markdown("---")

        col_widths = [
            0.6,
            0.65,
            0.42,
            0.75,
            0.65,
            0.65,
            0.65,
            0.55,
            0.85,
            0.55,
            0.42,
            0.38,
            0.38,
        ]
        header = st.columns(col_widths)
        for idx, column in enumerate(SORTABLE_COLUMNS):
            label = COLUMN_LABELS[column]
            with header[idx]:
                if st.button(
                    f"{label}{_positions_sort_indicator(column)}",
                    key=f"pos_sort_{column}",
                    type="tertiary",
                    use_container_width=True,
                ):
                    _positions_sort_click(column)
        header[11].markdown("**Chart**")
        header[12].markdown("**Sell**")

        for row in table_rows:
            p = row.position
            current = row.current_price
            live_price = row.live_price

            cols = st.columns(col_widths)
            cols[0].markdown(symbol_html(row), unsafe_allow_html=True)
            cols[1].write(str(row.quantity))
            cols[2].write(format_inr(row.avg_cost))
            cols[3].write(open_price_html(row, format_inr=format_inr))
            cols[4].write(session_high_html(row, format_inr=format_inr))
            cols[5].markdown(
                current_price_html(row, format_inr=format_inr),
                unsafe_allow_html=True,
            )
            cols[6].write(format_inr(row.market_value) if row.market_value is not None else "—")
            pnl = row.unrealized_pnl
            if pnl is not None:
                pnl_color = "#2e7d32" if pnl > 0 else "#c62828" if pnl < 0 else "#424242"
                cols[7].markdown(
                    f'<span style="color:{pnl_color};font-weight:600;">{format_inr(pnl)}</span>',
                    unsafe_allow_html=True,
                )
            else:
                cols[7].write("—")
            if row.target is not None:
                cols[8].write(format_inr(row.target))
            else:
                cols[8].write("—")
            cols[9].markdown(
                target_gap_html(row, format_inr=format_inr),
                unsafe_allow_html=True,
            )
            if row.stop_loss is not None:
                cols[10].write(format_inr(row.stop_loss))
            else:
                cols[10].write("—")
            if cols[11].button("Chart", key=f"pos_chart_{p.symbol}", type="secondary"):
                request_position_chart_dialog(
                    p.symbol,
                    live_price=current,
                    mark_price=float(p.mark_price) if p.mark_price is not None else None,
                )
                render_position_chart_dialog_if_open()
            if p.source == PositionSource.RECOMMENDATION:
                cols[12].button(
                    "Bracket",
                    key=f"pos_bracket_{p.symbol}",
                    disabled=True,
                    help="Rec positions exit only at target, stop, or 3:25 PM IST.",
                )
            elif cols[12].button("Sell", key=f"pos_sell_{p.symbol}", type="secondary"):
                try:
                    fill_price = Decimal(str(live_price)) if live_price is not None else None
                    order = run_async(
                        _place_order(
                            PlaceOrderRequest(
                                symbol=p.symbol,
                                side=OrderSide.SELL,
                                order_type=OrderType.MARKET,
                                quantity=p.quantity,
                            ),
                            market_fill_price=fill_price,
                        )
                    )
                    if order.status.value == "FILLED":
                        st.success(
                            f"Sold {p.quantity} × {p.symbol} @ {format_inr(order.filled_price)}"
                        )
                        st.session_state.pop("position_live_quotes", None)
                        st.session_state.pop("position_live_quotes_at", None)
                    elif order.status.value == "REJECTED":
                        st.error(f"Sell rejected for {p.symbol}.")
                    else:
                        st.info(f"Order {order.status.value} for {p.symbol}")
                    st.cache_data.clear()
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

        if live_quotes:
            st.caption(
                "* Live fields from NSE quote polling (current price and today's high)."
            )
        st.caption(
            "**Blue symbols** — recommendation bracket trades (exit at target, stop, or **3:25 PM IST**). "
            "**Black symbols** — manual trades (sell from the table or at **3:25 PM IST**). "
            "Current price is **green** when price is nearer target (darker = closer) and "
            "**red** when nearer stop loss. **To target** shows ₹/share remaining "
            "(total for qty in brackets); red when unrealized P&L is negative."
        )
    else:
        st.write("No open positions.")


@st.fragment(run_every=timedelta(seconds=1))
def _market_sync_progress_fragment() -> None:
    """Progress banner only — must not call _render_trading_page_body (it writes to sidebar)."""
    sync_jobs_to_session()

    if is_kind_running(JobKind.MARKET_SYNC):
        st.session_state["_market_sync_seen_running"] = True
        import time as _time

        from ui.background_jobs import _format_elapsed

        for job in list_jobs():
            if job["kind"] == JobKind.MARKET_SYNC.value:
                elapsed = _time.time() - float(job.get("started_at", _time.time()))
                msg = job.get("message", "Syncing market data…")
                st.info(f"Running in background: {msg} ({_format_elapsed(elapsed)})")
                st.progress(job.get("progress", 0.0), text=msg)
                st.caption("Market sync runs in the background — switch tabs freely.")
                break
        return

    if st.session_state.get("_market_sync_live_poll"):
        if st.session_state.get("_market_sync_seen_running"):
            st.session_state.pop("_market_sync_live_poll", None)
            st.session_state.pop("_market_sync_seen_running", None)
            st.rerun()
            return
        st.info("Starting market data sync…")
        st.caption("Market sync runs in the background — switch tabs freely.")
        return


@st.fragment(run_every=timedelta(seconds=LIVE_POLL_INTERVAL_SEC))
def _positions_auto_refresh_body() -> None:
    from ui.live_quote_poller import maybe_start_background_refresh, sync_cache_to_session
    from ui.streamlit_imports import is_hot_reload_import_error

    try:
        sync_cache_to_session(st.session_state)
        _positions_tab_content(auto_poll=True)
        symbols = st.session_state.get("_live_poll_symbols") or []
        maybe_start_background_refresh(symbols)
    except Exception as exc:
        if is_hot_reload_import_error(exc):
            return
        raise


@st.cache_resource
def _init_app():
    return ensure_ready()


def _ensure_recommendation_session_state() -> None:
    """Load today's session picks when session state is empty or from a prior trade date."""
    from app.services.market_calendar import active_market_session_date
    from app.services.recommendation_cache import load_cached_recommendations_for_ui
    from app.services.recommendation_engine import normalize_recommendation_report

    if is_kind_running(JobKind.RECOMMENDATIONS):
        return

    active = active_market_session_date()
    report = st.session_state.get("rec_report")
    if report is not None:
        normalize_recommendation_report(report)
        st.session_state["rec_report"] = report
    if (
        report is not None
        and not st.session_state.get("rec_from_cache", True)
        and report.prediction_date >= active
    ):
        st.session_state.pop("rec_stale_session", None)
        return

    cached = run_async(load_cached_recommendations_for_ui())

    if cached:
        new_report, allocation, cached_budget, cached_max_target, cached_at = cached
        existing_at = st.session_state.get("rec_cached_at")
        if existing_at is None or cached_at >= existing_at:
            st.session_state["rec_report"] = new_report
            st.session_state["rec_allocation"] = allocation
            st.session_state["rec_budget"] = cached_budget
            st.session_state["rec_max_target_pct"] = cached_max_target
            st.session_state["rec_from_cache"] = True
            st.session_state["rec_cached_at"] = cached_at
            st.session_state.pop("rec_stale_session", None)
            st.session_state.pop("rec_last_success", None)
            return

    if report is not None and report.prediction_date < active:
        st.session_state["rec_stale_session"] = True
    else:
        st.session_state.pop("rec_stale_session", None)


def _ensure_midday_session_state() -> None:
    """Load today's saved mid-day analysis when session state is empty."""
    from app.services.recommendation_cache import load_midday_cached_recommendations_for_ui
    from app.services.recommendation_engine import normalize_recommendation_report
    from app.services.simulation_cache import today_ist

    if is_kind_running(JobKind.MIDDAY_RECOMMENDATIONS):
        return

    today = today_ist()
    report = st.session_state.get("midday_report")
    if report is not None:
        normalize_recommendation_report(report)
        st.session_state["midday_report"] = report
    if (
        report is not None
        and not st.session_state.get("midday_from_cache", True)
        and report.data_through_date >= today
    ):
        return

    cached = load_midday_cached_recommendations_for_ui()
    if cached:
        new_report, allocation, cached_budget, cached_max_target, cached_at = cached
        existing_at = st.session_state.get("midday_cached_at")
        if existing_at is None or cached_at >= existing_at:
            st.session_state["midday_report"] = new_report
            st.session_state["midday_allocation"] = allocation
            st.session_state["midday_budget"] = cached_budget
            st.session_state["midday_max_target_pct"] = cached_max_target
            st.session_state["midday_from_cache"] = True
            st.session_state["midday_cached_at"] = cached_at
            st.session_state.pop("midday_last_success", None)
        return

    if report is not None and report.data_through_date < today:
        for key in (
            "midday_report",
            "midday_allocation",
            "midday_from_cache",
            "midday_cached_at",
            "midday_last_success",
        ):
            st.session_state.pop(key, None)


def _render_market_data_simulation_section(md_stats) -> None:
    st.subheader("Market data & simulation")
    st.caption(
        "All charts, backtests, and recommendations read from the **local market-data table**. "
        "Only **Refresh market data** on the sidebar connects to NSE to fill missing prices."
    )
    if md_stats.earliest_date and md_stats.latest_date:
        date_range = (
            f"{md_stats.earliest_date.strftime('%d %b %Y')} – "
            f"{md_stats.latest_date.strftime('%d %b %Y')}"
        )
    else:
        date_range = "—"

    if md_stats.simulation_saved_at:
        last_sim = md_stats.simulation_saved_at.strftime("%d %b %Y, %H:%M")
        if md_stats.simulation_universe:
            last_sim += f" ({md_stats.simulation_universe})"
        if md_stats.simulation_from_cache:
            last_sim += " · today's run"
    else:
        last_sim = "None — run Hard refresh on Pattern backtest tab"

    rec_report = st.session_state.get("rec_report")
    if rec_report and rec_report.top_patterns:
        eval_days = rec_report.eval_days
        top_patterns = ", ".join(
            f"{p.pattern_name} ({p.hit_rate_pct:g}%)" for p in rec_report.top_patterns
        )
        top_patterns_label = (
            f"Top 3 recommendation patterns ({eval_days}-day BUY success, same as Recommendations tab)"
        )
    elif md_stats.top_patterns:
        eval_days = md_stats.recommendation_eval_days or 15
        top_patterns = ", ".join(f"{name} ({rate}%)" for name, rate in md_stats.top_patterns)
        top_patterns_label = (
            f"Top 3 recommendation patterns ({eval_days}-day BUY success · run analysis to sync)"
        )
    else:
        top_patterns = "—"
        top_patterns_label = "Top 3 recommendation patterns (30-day BUY success)"

    st.table(
        pd.DataFrame(
            [
                {"Stat": "Unique stocks with OHLCV data", "Value": md_stats.stocks_with_data},
                {"Stat": "OHLCV date range", "Value": date_range},
                {"Stat": "Last simulation in database", "Value": last_sim},
                {"Stat": top_patterns_label, "Value": top_patterns},
            ]
        )
    )

    if is_kind_running(JobKind.MARKET_SYNC):
        for job in list_jobs():
            if job["kind"] == JobKind.MARKET_SYNC.value:
                st.progress(job.get("progress", 0.0), text=job.get("message", "Syncing market data…"))
                break

    if st.session_state.get("market_sync_last_error"):
        st.error(st.session_state.pop("market_sync_last_error"))
    if st.session_state.get("market_sync_last_success"):
        st.success(st.session_state.pop("market_sync_last_success"))


def _render_positions_tab(positions) -> None:
    from app.services.market_calendar import is_live_quote_session
    from ui.live_quote_poller import maybe_start_background_refresh, sync_cache_to_session

    poll_col, fetch_col, reconcile_col = st.columns([1.1, 1, 1.2])
    with poll_col:
        live_polling = st.toggle(
            "Live polling (10s)",
            value=st.session_state.get("live_polling_enabled", True),
            key="live_polling_enabled",
            help=(
                "During market hours, auto-fetch NSE quotes every 10s in the background, "
                "process bracket entry/target/stop, and square off at 3:25 PM IST."
            ),
        )
    with fetch_col:
        fetch_live = st.button(
            "Fetch live prices",
            key="fetch_position_live_prices",
            disabled=live_polling and is_live_quote_session(),
        )
    with reconcile_col:
        reconcile_now = st.button(
            "Reconcile brackets",
            key="reconcile_bracket_positions",
            help=(
                "Re-check open bracket plans against NSE session high/low and process "
                "any missed entries, targets, or stops (e.g. while the app was offline)."
            ),
        )

    if fetch_live:
        try:
            got_quotes, _ = _apply_live_trading_refresh(
                st.session_state.get("_live_poll_symbols") or []
            )
            if not got_quotes:
                st.warning("No live quotes returned — try again during market hours.")
            st.session_state.pop("live_poll_error", None)
            st.rerun()
        except Exception as exc:
            st.error(f"Live quote fetch failed: {exc}")

    if reconcile_now:
        try:
            stats = run_async(_reconcile_brackets_if_needed(force=True))
            from app.services.bracket_reconcile_state import format_reconcile_notice

            notice = format_reconcile_notice(
                stats or {},
                prefix="Manual bracket reconcile",
            )
            st.session_state["trade_plan_live_notice"] = notice or (
                "Manual bracket reconcile: no changes."
            )
            st.session_state.pop("live_poll_error", None)
            st.rerun()
        except Exception as exc:
            st.error(f"Bracket reconcile failed: {exc}")

    from app.services.bracket_reconcile_state import load_bracket_reconcile_state

    bracket_state = load_bracket_reconcile_state()
    timing_parts: list[str] = []
    if bracket_state.last_reconcile_at:
        timing_parts.append(
            f"Last reconcile {format_ist_datetime(bracket_state.last_reconcile_at)} IST"
        )
    if bracket_state.last_live_poll_at:
        timing_parts.append(
            f"last live poll {format_ist_datetime(bracket_state.last_live_poll_at)} IST"
        )
    if timing_parts:
        st.caption(" · ".join(timing_parts))

    poll_symbols = {p.symbol for p in positions}
    if live_polling and is_live_quote_session():
        session_orders = run_async(_orders())
        poll_symbols.update(o.symbol for o in session_orders)
    st.session_state["_live_poll_symbols"] = sorted(poll_symbols)

    if live_polling and is_live_quote_session():
        sync_cache_to_session(st.session_state)
        maybe_start_background_refresh(st.session_state["_live_poll_symbols"])
        _positions_auto_refresh_body()
    else:
        _positions_tab_content(auto_poll=False)


def _render_orders_tab() -> None:
    from app.services.market_calendar import (
        active_market_session_date,
        current_session_date,
        is_live_quote_session,
    )

    session_day = current_session_date()
    live_polling = st.session_state.get("live_polling_enabled", True)
    st.caption(f"Showing orders for **{session_day.strftime('%d %b %Y')}** (IST) only.")
    cleanup = run_async(_cleanup_duplicate_session_orders())
    cleanup_parts = []
    if cleanup.get("cancelled_orders"):
        cleanup_parts.append(
            f"{cleanup['cancelled_orders']} duplicate pending order(s) cancelled"
        )
    if cleanup.get("undone_fills"):
        cleanup_parts.append(f"{cleanup['undone_fills']} duplicate fill(s) reversed")
    if cleanup.get("cancelled_rejected_exits"):
        cleanup_parts.append(
            f"{cleanup['cancelled_rejected_exits']} failed bracket exit order(s) cleared"
        )
    if cleanup.get("reconciled_plans"):
        cleanup_parts.append(
            f"{cleanup['reconciled_plans']} stuck bracket plan(s) reconciled"
        )
    if cleanup_parts:
        st.info("Order cleanup: " + "; ".join(cleanup_parts) + ".")
    orders = run_async(_orders())
    st.session_state["_session_orders"] = orders
    if orders:
        plan_day = active_market_session_date()
        by_order, by_symbol = run_async(_load_order_bracket_context(plan_day))
        summary = run_async(_market_summary())

        from ui.live_quote_poller import sync_cache_to_session
        from ui.orders_display import resolve_order_current_price

        sync_cache_to_session(st.session_state)
        quote_cache = st.session_state.get("position_live_quotes", {})

        close_by_symbol = {
            s.symbol: float(s.last_close) for s in summary if s.last_close is not None
        }

        if live_polling and is_live_quote_session():
            st.caption(
                "Current price from live NSE feed (*) when polled; "
                "otherwise last synced close."
            )
        elif not is_live_quote_session():
            st.caption("Outside market hours — current price shows last close when available.")

        def _fmt_price(value: float | None) -> str:
            return format_inr(value) if value is not None else "—"

        def _fmt_current(current: float | None, *, is_live: bool) -> str:
            if current is None:
                return "—"
            label = _fmt_price(current)
            if is_live and live_polling and is_live_quote_session():
                return f"{label} *"
            return label

        def _order_price_context(order):
            current, is_live = resolve_order_current_price(
                order.symbol,
                live_quotes=quote_cache,
                close_by_symbol=close_by_symbol,
            )

            target_buy, target_sell = by_order.get(order.id) or by_symbol.get(
                order.symbol, (None, None)
            )
            if target_buy is None and order.limit_price is not None:
                target_buy = float(order.limit_price)
            return current, is_live, target_buy, target_sell

        hdr = st.columns([1.0, 0.55, 0.65, 0.45, 0.75, 0.85, 0.85, 0.85, 0.55])
        for col, label in zip(
            hdr,
            [
                "Symbol",
                "Side",
                "Type",
                "Qty",
                "Status",
                "Current price",
                "Target buy",
                "Target sell",
                "Action",
            ],
        ):
            col.markdown(f"**{label}**")

        for order in orders[:20]:
            current, is_live, target_buy, target_sell = _order_price_context(order)
            cols = st.columns([1.0, 0.55, 0.65, 0.45, 0.75, 0.85, 0.85, 0.85, 0.55])
            cols[0].write(f"**{order.symbol}**")
            cols[1].write(order.side.value)
            cols[2].write(order.order_type.value)
            cols[3].write(str(order.quantity))
            cols[4].write(order.status.value)
            cols[5].write(_fmt_current(current, is_live=is_live))
            cols[6].write(_fmt_price(target_buy))
            cols[7].write(_fmt_price(target_sell))
            if order.status.value == "PENDING" and cols[8].button(
                "Cancel", key=f"cancel-{order.id}"
            ):
                run_async(_cancel_order(order.id))
                st.cache_data.clear()
                st.rerun()
    else:
        st.write(f"No orders for {session_day.strftime('%d %b %Y')}.")


def _render_trades_tab() -> None:
    from app.services.market_calendar import current_session_date

    session_day = current_session_date()
    st.caption(f"Showing trades for **{session_day.strftime('%d %b %Y')}** (IST) only.")
    trades = run_async(_trades())
    if trades:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Symbol": t.symbol,
                        "Side": t.side.value,
                        "Qty": t.quantity,
                        "Price": format_inr(t.price),
                        "Realized P&L": format_inr(t.realized_pnl),
                        "Executed": format_ist_datetime(t.executed_at),
                    }
                    for t in trades[:30]
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.write(f"No trades for {session_day.strftime('%d %b %Y')}.")


def _render_nifty250_constituents_tab() -> None:
    from app.services.market_calendar import active_market_session_date

    _ensure_recommendation_session_state()
    session_date = active_market_session_date()
    summary = run_async(_market_summary())
    recommended = run_async(_recommended_symbols_for_session(session_date))
    stocks = [s for s in summary if s.instrument_type.value == "EQUITY"]
    rows = []
    for s in stocks:
        change = s.change_pct if s.change_pct is not None else 0.0
        is_rec = s.symbol in recommended
        rows.append(
            {
                "Symbol": s.symbol,
                "Name": s.name,
                "Last close": format_inr(s.last_close),
                "Change %": format_pct(s.change_pct),
                "Profitable day": "Yes" if change > 0 else "No",
                "Recommended": "Yes" if is_rec else "No",
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty:

        def _style_nifty250(row):
            styles = [""] * len(row)
            rec_idx = row.index.get_loc("Recommended")
            if row["Recommended"] == "Yes":
                styles[rec_idx] = "background-color: #e3f2fd; color: #1565c0; font-weight: 600"
            elif row.get("Profitable day") == "Yes":
                styles[rec_idx] = "background-color: #fff3e0; color: #ef6c00; font-weight: 600"
            return styles

        st.caption(
            f"NIFTY250 constituents for **{session_date.strftime('%d %b %Y')}** · "
            "**Recommended** = in today's recommendation picks · "
            "orange = profitable close not recommended."
        )
        st.dataframe(
            df.style.apply(_style_nifty250, axis=1),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.write("No constituent data.")


def _render_trading_data_tabs(positions) -> None:
    """Lazy-load tab bodies — only the selected view runs DB work on each rerun."""
    active = st.radio(
        "Trading data",
        ["Positions", "Orders", "Trades", "NIFTY250"],
        horizontal=True,
        key="trading_data_tab",
        label_visibility="collapsed",
    )

    if active == "Positions":
        _render_positions_tab(positions)
    elif active == "Orders":
        _render_orders_tab()
    elif active == "Trades":
        _render_trades_tab()
    elif active == "NIFTY250":
        _render_nifty250_constituents_tab()


def _broker_charge_breakdown_dataframe(sharekhan, zerodha) -> pd.DataFrame:
    """Side-by-side charge rows for Sharekhan vs Zerodha (readable in expander)."""

    def _charge_row(label: str, attr: str) -> dict[str, str]:
        return {
            "Charge": label,
            "Sharekhan": format_inr(getattr(sharekhan, attr, 0.0)),
            "Zerodha": format_inr(getattr(zerodha, attr, 0.0)),
        }

    return pd.DataFrame(
        [
            _charge_row("STT", "total_stt"),
            _charge_row("Stamp duty", "total_stamp_duty"),
            _charge_row("Brokerage", "total_brokerage"),
            _charge_row("DP charges", "total_dp_charges"),
            _charge_row("NSE / SEBI / GST", "total_exchange_sebi_gst"),
            _charge_row("STCG tax", "total_stcg_tax"),
            {
                "Charge": "Total deductions",
                "Sharekhan": format_inr(sharekhan.total_tax_and_charges),
                "Zerodha": format_inr(zerodha.total_tax_and_charges),
            },
            {
                "Charge": "Net after tax",
                "Sharekhan": format_inr(sharekhan.net_after_tax),
                "Zerodha": format_inr(zerodha.net_after_tax),
            },
        ]
    )


def _render_broker_portfolio_comparison(
    portfolio,
    sharekhan_after_tax,
    zerodha_after_tax,
) -> None:
    from app.services.budget_portfolio import (
        portfolio_total_at_cost,
        portfolio_total_with_unrealized,
    )

    sk_at_cost = portfolio_total_at_cost(
        portfolio.invested_cost,
        portfolio.cash_available,
        sharekhan_after_tax.net_after_tax,
    )
    sk_with_unrealized = portfolio_total_with_unrealized(
        portfolio.invested_cost,
        portfolio.cash_available,
        sharekhan_after_tax.net_after_tax,
        portfolio.unrealized_pnl,
    )
    zd_at_cost = portfolio_total_at_cost(
        portfolio.invested_cost,
        portfolio.cash_available,
        zerodha_after_tax.net_after_tax,
    )
    zd_with_unrealized = portfolio_total_with_unrealized(
        portfolio.invested_cost,
        portfolio.cash_available,
        zerodha_after_tax.net_after_tax,
        portfolio.unrealized_pnl,
    )

    st.markdown("**After-tax P&L & total value (delivery)**")
    col_sk, col_zd = st.columns(2)
    with col_sk:
        st.markdown("Sharekhan")
        m1, m2 = st.columns(2)
        m1.metric("Realized after tax", format_inr(sharekhan_after_tax.net_after_tax))
        m2.metric("Total value", format_inr(sk_at_cost))
        st.caption(f"Incl. unrealized: {format_inr(sk_with_unrealized)}")
    with col_zd:
        st.markdown("Zerodha")
        m3, m4 = st.columns(2)
        m3.metric("Realized after tax", format_inr(zerodha_after_tax.net_after_tax))
        m4.metric("Total value", format_inr(zd_at_cost))
        st.caption(f"Incl. unrealized: {format_inr(zd_with_unrealized)}")

    with st.expander("Tax & charge breakdown (Sharekhan vs Zerodha)"):
        st.dataframe(
            _broker_charge_breakdown_dataframe(sharekhan_after_tax, zerodha_after_tax),
            use_container_width=True,
            hide_index=True,
        )


def _render_paper_trading_after_tax_section() -> None:
    from app.config import settings
    from app.defaults import DEFAULT_DAILY_TRADING_BUDGET_INR
    from app.services.budget_portfolio import compute_budget_view
    from ui.streamlit_imports import ensure_budget_portfolio_fresh, ensure_trade_tax_fresh

    ensure_budget_portfolio_fresh()
    ensure_trade_tax_fresh()
    default_budget = float(
        getattr(settings, "daily_trading_budget_inr", DEFAULT_DAILY_TRADING_BUDGET_INR)
    )
    budget = float(st.session_state.get("rec_budget", default_budget))
    positions = run_async(_positions())
    portfolio = compute_budget_view(budget, positions)
    realized_dual = run_async(_realized_pnl_after_tax_summary())
    _render_broker_portfolio_comparison(
        portfolio,
        realized_dual.sharekhan,
        realized_dual.zerodha,
    )
    st.caption(
        "**After-tax P&L** and **total value** compare "
        "[Sharekhan delivery](https://www.sharekhan.com/pricing) (0.30%/side brokerage) vs "
        "[Zerodha delivery](https://zerodha.com/charges/#tab-equities) (zero brokerage); "
        "both include STT, stamp duty, NSE txn, SEBI, GST, STCG, and DP debit on sell "
        "(Zerodha ₹15.34/scrip; Sharekhan nil via broker). "
        "**Total value** = invested + cash available + after-tax realized P&L; "
        "bracket adds unrealized P&L on open positions."
    )


def render_trading_page():
    if st.session_state.pop("_market_sync_requested", False):
        if is_any_job_running() and not is_kind_running(JobKind.MARKET_SYNC):
            st.warning("Wait for the current background task to finish.")
        elif start_market_sync_job():
            st.session_state["_market_sync_live_poll"] = True

    sync_jobs_to_session()

    sync_running = is_kind_running(JobKind.MARKET_SYNC)
    if st.session_state.get("_market_sync_live_poll") and not sync_running:
        # Sync finished — clear poll flags; body below loads fresh data this run.
        st.session_state.pop("_market_sync_live_poll", None)
        st.session_state.pop("_market_sync_seen_running", None)

    # Banner only — never skip the trading page body (that caused blank "App shell ready").
    if sync_running or st.session_state.get("_market_sync_live_poll"):
        _market_sync_progress_fragment()

    _render_trading_page_body()


def _render_trading_page_body():
    from ui.streamlit_imports import (
        ensure_budget_portfolio_fresh,
        ensure_defaults_fresh,
    )

    ensure_defaults_fresh()
    ensure_budget_portfolio_fresh()
    from app.config import settings
    from app.defaults import DEFAULT_DAILY_TRADING_BUDGET_INR, DEFAULT_MAX_TARGET_PROFIT_PCT
    from app.services.budget_portfolio import compute_budget_view

    default_budget = float(
        getattr(settings, "daily_trading_budget_inr", DEFAULT_DAILY_TRADING_BUDGET_INR)
    )
    budget = float(st.session_state.get("rec_budget", default_budget))

    render_symbol_history_chart_dialog_if_open()

    show_market_data = st.session_state.get("trading_footer_section") == "Market data & simulation"
    instruments, account, _summary, md_stats, positions = run_async(
        _load_trading_page_data(
            budget_inr=budget,
            include_summary=False,
            include_md_stats=show_market_data,
        )
    )
    portfolio = compute_budget_view(budget, positions)

    st.session_state["_trading_positions"] = positions
    poll_symbols = {p.symbol for p in positions}
    st.session_state["_live_poll_symbols"] = sorted(poll_symbols)
    from ui.live_quote_poller import sync_cache_to_session

    sync_cache_to_session(st.session_state)
    chart_instruments = [i for i in instruments if i.instrument_type.value != "INDEX"]
    if not chart_instruments:
        chart_instruments = list(instruments)
    labels = {f"{i.symbol} — {i.name}": i.symbol for i in chart_instruments}
    default_symbol = st.session_state.get("selected_symbol")
    if not default_symbol or default_symbol == "NIFTY50" or default_symbol not in labels.values():
        default_symbol = chart_instruments[0].symbol if chart_instruments else "RELIANCE"

    with st.sidebar:
        st.caption(f"{len(instruments)} symbols in database")
        if labels:
            selected_label = st.selectbox(
                "Symbol",
                options=list(labels.keys()),
                index=next(
                    (idx for idx, sym in enumerate(labels.values()) if sym == default_symbol),
                    0,
                ),
                key="chart_symbol",
            )
            selected_symbol = labels[selected_label]
        else:
            selected_symbol = default_symbol
            st.caption("No chart symbols in database yet.")
        chart_days = st.slider("Days", min_value=7, max_value=90, value=30, key="chart_days")
        if st.button("Show chart", key="show_symbol_chart", use_container_width=True):
            request_symbol_history_chart_dialog(
                selected_symbol,
                days=int(chart_days),
            )
            render_symbol_history_chart_dialog_if_open()

        st.divider()
        st.header("Place order")
        side = st.selectbox("Side", ["BUY", "SELL"], key="order_side")
        order_type = st.selectbox("Type", ["MARKET", "LIMIT"], key="order_type")
        quantity = st.number_input("Quantity", min_value=1, value=1, step=1, key="order_qty")
        limit_price = None
        if order_type == "LIMIT":
            limit_price = st.number_input(
                "Limit price (₹)", min_value=0.01, value=100.0, step=0.05, key="limit_price"
            )

        if (
            side == "SELL"
        ):
            bracket_symbols = run_async(_recommendation_bracket_symbols())
            if selected_symbol.upper() in {s.upper() for s in bracket_symbols}:
                st.warning(
                    f"**{selected_symbol}** has an active recommendation bracket — "
                    "sell only via target, stop loss, or **3:25 PM IST** square-off."
                )

        if st.button("Submit order", type="primary", use_container_width=True):
            try:
                run_async(
                    _place_order(
                        PlaceOrderRequest(
                            symbol=selected_symbol,
                            side=OrderSide(side),
                            order_type=OrderType(order_type),
                            quantity=int(quantity),
                            limit_price=Decimal(str(limit_price)) if order_type == "LIMIT" else None,
                        ),
                        budget_inr=budget,
                    )
                )
                st.success("Order submitted")
                st.cache_data.clear()
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

    st.subheader("Portfolio summary")
    st.table(
        pd.DataFrame(
            [
                {"Metric": "Daily budget", "Value": format_inr(portfolio.budget_inr)},
                {"Metric": "Invested", "Value": format_inr(portfolio.invested_cost)},
                {"Metric": "Cash available", "Value": format_inr(portfolio.cash_available)},
                {"Metric": "Equity (market)", "Value": format_inr(portfolio.equity_market_value)},
                {"Metric": "Unrealized P&L", "Value": format_inr(portfolio.unrealized_pnl)},
                {"Metric": "Realized P&L (gross)", "Value": format_inr(account.realized_pnl)},
            ]
        )
    )
    st.caption(
        "Tracks your daily budget and open positions. "
        "**Rec** positions come from Recommendations; **Manual** from the sidebar Place order. "
        "Everything is auto-sold at **3:25 PM IST**. "
        "After-tax broker comparison is on **Paper trading trend**."
    )

    _render_trading_data_tabs(positions)

    footer_section = st.radio(
        "Trading page footer",
        ["Summary only", "Market data & simulation"],
        horizontal=True,
        key="trading_footer_section",
        label_visibility="collapsed",
    )
    if footer_section == "Market data & simulation":
        if md_stats is None:
            md_stats = run_async(_market_data_stats())
        _ensure_recommendation_session_state()
        rec_report = st.session_state.get("rec_report")
        if st.session_state.get("rec_stale_session") and rec_report:
            from app.services.market_calendar import active_market_session_date

            active = active_market_session_date()
            st.warning(
                f"Saved recommendations are for **{rec_report.prediction_date.strftime('%d %b %Y')}**. "
                f"Run **Recommendation analysis** on the Recommendations tab for today's session "
                f"(**{active.strftime('%d %b %Y')}**)."
            )
        st.markdown("---")
        _render_market_data_simulation_section(md_stats)


def _render_prediction_validation(report, *, title_prefix: str = "Today's prediction validation"):
    pred_date, data_through, lookback = prediction_context(report)
    if pred_date is None:
        return

    scorecard = build_validation_scorecard(report)
    summary_df, meta_df, _ = build_bullish_summary_matrix(report)
    if summary_df.empty:
        st.warning("No pattern signals for the latest trading day.")
        return

    st.subheader(f"{title_prefix} — {pred_date.strftime('%d %b %Y')}")
    if data_through and lookback:
        st.info(
            f"**Method:** Each pattern uses a **{lookback}-day lookback ending {data_through.strftime('%d %b %Y')}** "
            f"(data through yesterday). That produces a direction + target price for "
            f"**{pred_date.strftime('%d %b %Y')}**. Prices are official closing prices stored in the market-data table "
            f"(not LTP). Validation compares against actual close on {pred_date.strftime('%d %b %Y')} vs the prior day."
        )
    elif lookback:
        st.caption(f"{lookback}-day lookback · validated against actual close on {pred_date.isoformat()}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Overall hit rate", f"{scorecard['total_hits']}/{scorecard['total_signals']}", f"{scorecard['overall_hit_rate']}%")
    c2.metric("Bullish correct", f"{scorecard['bullish_hits']}/{scorecard['bullish_total']}")
    c3.metric("Bearish correct", f"{scorecard['bearish_hits']}/{scorecard['bearish_total']}")
    c4.metric("Stocks in universe", report.stock_count if hasattr(report, "stock_count") else "15")

    st.markdown("**Pattern scorecard (today's session)**")
    st.dataframe(scorecard["pattern_breakdown"], use_container_width=True, hide_index=True)

    st.markdown("**Bullish signals — predicted vs actual**")
    st.caption(
        "Green = pattern predicted **BULLISH** and stock closed up vs prior day. "
        "Red = predicted bullish but closed down. **Off %** = actual vs predicted target price."
    )
    st.dataframe(style_bullish_summary(summary_df, meta_df), use_container_width=True, height=560)

    bearish_df = build_bearish_mismatch_summary(report)
    if not bearish_df.empty:
        with st.expander("Bearish predictions on same day"):
            st.dataframe(bearish_df, use_container_width=True, hide_index=True)


def render_backtest_page():
    from app.config import settings
    from app.defaults import DEFAULT_SIMULATION_UNIVERSE
    from app.services.nifty_universe import (
        DEFAULT_UNIVERSE,
        get_universe_config,
        list_universe_options,
        universe_label,
    )

    st.subheader("Pattern Backtesting")

    universe_options = list_universe_options()
    default_uni = st.session_state.get(
        "sim_universe",
        getattr(settings, "default_simulation_universe", DEFAULT_SIMULATION_UNIVERSE),
    )
    if default_uni not in universe_options:
        default_uni = DEFAULT_UNIVERSE

    universe = st.selectbox(
        "Simulation universe",
        options=universe_options,
        index=universe_options.index(default_uni),
        format_func=universe_label,
        help="Stock set for pattern backtest and today's validation. Default: NIFTY250.",
    )
    st.session_state["sim_universe"] = universe
    uni_cfg = get_universe_config(universe)
    patterns = list_registered_patterns()
    st.caption(
        f"{len(patterns)} patterns · **{uni_cfg['stock_count']} stocks** ({universe}) · local market data · "
        f"{uni_cfg['lookback_days']}-day lookback · {uni_cfg['eval_days']}-day eval"
    )

    st.info(f"**{len(patterns)} patterns** registered — Doji, Hammer, Bollinger, and more.")

    backtest_section = st.radio(
        "Backtest section",
        ["Today's validation", "30-day simulation"],
        horizontal=True,
        key="backtest_page_section",
        label_visibility="collapsed",
    )

    if backtest_section == "Today's validation":
        validate_clicked = st.button(
            "Validate today's predictions (data through yesterday)",
            type="primary",
            disabled=is_kind_running(JobKind.TODAY_PREDICTION),
            key="validate_today_btn",
        )

        if validate_clicked:
            if is_any_job_running() and not is_kind_running(JobKind.TODAY_PREDICTION):
                st.warning("Wait for the current background task to finish.")
            elif start_today_prediction_job(universe):
                st.rerun()

        if st.session_state.get("today_pred_last_error"):
            st.error(st.session_state.pop("today_pred_last_error"))
        if st.session_state.get("today_pred_last_success"):
            st.success(st.session_state.pop("today_pred_last_success"))

        if is_kind_running(JobKind.TODAY_PREDICTION):
            for job in list_jobs():
                if job["kind"] == JobKind.TODAY_PREDICTION.value:
                    st.info(f"Running in background: {job['message']}")
                    break

        today_report = st.session_state.get("today_prediction_report")
        if today_report:
            st.markdown("---")
            _render_prediction_validation(today_report)
        else:
            st.caption(
                "Click **Validate today's predictions** to score the latest session against stored closes."
            )
        return

    st.markdown("---")
    st.subheader("30-day historical simulation")

    sim_report = st.session_state.get("live_sim_report")
    sim_run_id = st.session_state.get("live_sim_run_id")
    sim_run_at = st.session_state.get("live_sim_run_at")
    sim_from_cache = st.session_state.get("live_sim_from_cache", False)

    sim_running = is_kind_running(JobKind.SIM_BACKTEST)
    if not sim_report and not sim_running:
        cached_report, cached_run_id, cached_run_at = run_async(load_cached_simulation(universe))
        if cached_report is not None:
            st.session_state["live_sim_report"] = cached_report
            st.session_state["live_sim_run_id"] = cached_run_id
            st.session_state["live_sim_run_at"] = cached_run_at
            st.session_state["live_sim_from_cache"] = True
            st.session_state["live_sim_universe"] = universe
            sim_report = cached_report
            sim_run_id = cached_run_id
            sim_run_at = cached_run_at
            sim_from_cache = True
        elif st.session_state.get("live_sim_universe") != universe:
            st.session_state.pop("live_sim_report", None)
            st.session_state.pop("live_sim_run_id", None)
            st.session_state.pop("live_sim_run_at", None)
            st.session_state["live_sim_from_cache"] = False
            st.session_state["live_sim_universe"] = universe

    refresh_col, info_col = st.columns([1, 3])
    with refresh_col:
        hard_refresh = st.button(
            "Hard refresh",
            type="secondary",
            disabled=is_kind_running(JobKind.SIM_BACKTEST),
            help="Re-run the full simulation in the background. Missing market history is backfilled automatically.",
        )
    with info_col:
        if sim_report and sim_run_at:
            source = "cached snapshot" if sim_from_cache else "fresh run"
            st.caption(
                f"Showing **{source}** for **{universe}** "
                f"(saved {sim_run_at.strftime('%d %b %Y, %H:%M IST') if hasattr(sim_run_at, 'strftime') else sim_run_at}). "
                "Simulations run at most once per day unless you hard refresh."
            )
        elif not sim_report:
            st.caption(
                "No simulation stored for today yet. Use **Hard refresh** to run once — "
                "results are saved and reused until tomorrow or the next hard refresh."
            )

    if hard_refresh:
        if is_any_job_running() and not is_kind_running(JobKind.SIM_BACKTEST):
            st.warning("Wait for the current background task to finish before hard refresh.")
        elif start_sim_backtest_job(universe, uni_cfg["stock_count"]):
            st.rerun()

    if st.session_state.get("sim_last_error"):
        st.error(st.session_state.pop("sim_last_error"))
    if st.session_state.get("sim_last_success"):
        st.success(st.session_state.pop("sim_last_success"))

    sim_running = is_kind_running(JobKind.SIM_BACKTEST)
    if sim_running:
        for job in list_jobs():
            if job["kind"] == JobKind.SIM_BACKTEST.value:
                st.info(f"Running in background: {job['message']}")
                st.progress(job.get("progress", 0.0), text=job.get("message", "Running simulation…"))
                st.caption("Simulation runs in the background — switch tabs freely.")
                if st.button("Cancel simulation", key="cancel_sim_job"):
                    cancel_running_job(JobKind.SIM_BACKTEST)
                    st.rerun()
                break

    if not sim_report:
        if sim_running:
            return
        st.markdown(
            "Load today's saved simulation or run **Hard refresh** to compute and store a new snapshot."
        )
        with st.expander("Patterns that will be tested", key="sim_patterns_expander"):
            for p in patterns:
                st.write(f"- **{p.name}**")
        return

    # Latest day from full backtest (should match today validation when data is current)
    pred_date, data_through, _ = prediction_context(sim_report)
    if pred_date:
        with st.expander(
            f"Latest day from 30-day sim ({pred_date.isoformat()}) — same methodology as above"
        ):
            _render_prediction_validation(
                sim_report,
                title_prefix="Latest eval day from simulation",
            )

    st.success(
        "Simulation loaded — full backtest rankings below."
        + (" (from today's cache)" if sim_from_cache else "")
    )

    rows = []
    for rank, pr in enumerate(sim_report.patterns, start=1):
        rows.append(
            {
                "Rank": rank,
                "Pattern": pr.pattern_name,
                "Avg correct/day": f"{pr.avg_daily_score:.1f}/{sim_report.stock_count}",
                "Hit rate %": round(pr.overall_hit_rate, 1),
                "Total correct": pr.total_correct,
                "Signals": pr.total_signals,
                "ID": pr.pattern_id,
            }
        )
    df = pd.DataFrame(rows)
    st.dataframe(df.drop(columns=["ID"]), use_container_width=True, hide_index=True)
    st.bar_chart(df.set_index("Pattern")[["Hit rate %"]])

    pattern_map = {r["ID"]: r["Pattern"] for r in rows}
    selected_id = st.selectbox(
        "Pattern detail",
        options=[r["ID"] for r in rows],
        format_func=lambda pid: pattern_map.get(pid, pid),
    )
    if selected_id:
        _render_pattern_detail(selected_id, sim_report, sim_run_id)


def _render_pattern_detail(selected_id: str, sim_report, sim_run_id: int | None) -> None:
    from fastapi import HTTPException

    detail = None
    if sim_report:
        pr = next((p for p in sim_report.patterns if p.pattern_id == selected_id), None)
        if pr is not None:
            detail = pattern_detail_from_result(pr)

    if detail is None and sim_run_id:
        try:
            detail = run_async(_backtest_pattern_detail(sim_run_id, selected_id))
        except HTTPException as exc:
            st.warning(f"Pattern detail unavailable from database: {exc.detail}")

    if detail is None:
        return

    st.write(f"**{detail['pattern_name']}** — per-stock hit rate")
    if detail.get("stock_scores"):
        st.dataframe(pd.DataFrame(detail["stock_scores"]), use_container_width=True, hide_index=True)
    if detail.get("day_details"):
        st.write("Predicted vs actual close (all signals)")
        st.dataframe(
            day_details_dataframe(detail["day_details"]),
            use_container_width=True,
            hide_index=True,
        )


def _render_recommendation_chart_panel(report, symbol: str) -> None:
    from app.services.recommendation_engine import all_report_recommendations
    from app.services.trade_tax import compute_net_profit

    rec = next((r for r in all_report_recommendations(report) if r.symbol == symbol), None)
    if rec is None:
        st.warning(f"No recommendation data for {symbol}.")
        return

    hdr_l, hdr_r = st.columns([5, 1])
    hdr_l.subheader(f"Why {rec.symbol} is recommended")
    if hdr_r.button("Close chart", key="rec_chart_close"):
        st.session_state.pop("rec_chart_symbol", None)
        st.rerun()

    tax = compute_net_profit(1, rec.buy_price, rec.actual_sell_price)
    st.caption(
        f"**{rec.pattern_name}** · 15d hit {rec.pattern_hit_rate_30d:.0f}% · "
        f"confidence {rec.confidence_score:.0f} · BUY ref ₹{rec.buy_price:,.2f} · "
        f"profit after tax (1 sh) ₹{tax.net_profit_after_tax:,.2f}"
    )

    candles = run_async(_candles(rec.symbol, 45))
    if not candles:
        st.info("No candle data — click **Refresh market data** on the Trading tab, then re-open this chart.")
        return

    fig = build_recommendation_chart(rec, candles, lookback_days=report.lookback_days)
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "Highlighted zone = detected pattern · dotted line = prior downtrend · "
        "green arrow = expected bullish move · horizontal lines = stop / resistance / targets"
    )


def _render_recommendation_tier_table(tier_recs, report, tier_label: str) -> None:
    from app.services.trade_tax import compute_net_profit

    hdr = st.columns([0.9, 1.4, 0.7, 0.7, 0.9, 0.9, 0.9, 0.9, 0.7])
    for col, label in zip(
        hdr,
        [
            "Stock",
            "Pattern",
            "Hit %",
            "Conf.",
            "Buy",
            "Target",
            "Profit pre-tax",
            "Profit post-tax",
            "Chart",
        ],
    ):
        col.markdown(f"**{label}**")

    for rec in tier_recs:
        tax = compute_net_profit(1, rec.buy_price, rec.actual_sell_price)
        cols = st.columns([0.9, 1.4, 0.7, 0.7, 0.9, 0.9, 0.9, 0.9, 0.7])
        cols[0].write(f"**{rec.symbol}**")
        cols[1].write(rec.pattern_name)
        cols[2].write(f"{rec.pattern_hit_rate_30d:.0f}")
        cols[3].write(f"{rec.confidence_score:.0f}")
        cols[4].write(format_inr(rec.buy_price))
        cols[5].write(f"{format_inr(rec.sell_price)} ({rec.model_profit_pct:+.0f}%)")
        cols[6].write(format_inr(tax.profit_before_tax))
        cols[7].write(format_inr(tax.net_profit_after_tax))
        if cols[8].button("Chart", key=f"rec_chart_{tier_label}_{rec.symbol}", type="secondary"):
            st.session_state["rec_chart_symbol"] = rec.symbol
            st.rerun()

    with st.expander(f"Full details — {tier_label}"):
        tier_df = recommendations_dataframe(
            tier_recs,
            max_target_profit_pct=report.max_target_profit_pct,
        ).drop(columns=["Cap tier"])
        st.dataframe(tier_df, use_container_width=True, hide_index=True)


def _render_budget_simulation_section(
    report,
    *,
    tier_budget_split_pct: float,
    trading_budget_inr: float,
) -> None:
    """What-if share counts at alternate budgets — read-only, no order placement."""
    from app.defaults import DEFAULT_DAILY_TRADING_BUDGET_INR
    from app.services.budget_allocator import allocate_budget

    st.markdown("---")
    st.subheader("Budget simulation")
    st.caption(
        "Try different budgets against the same recommendations — share counts and "
        "investment only. This does not change your trading budget above or place orders."
    )

    if "rec_sim_budget" not in st.session_state:
        st.session_state["rec_sim_budget"] = float(
            st.session_state.get("rec_budget", trading_budget_inr)
            or DEFAULT_DAILY_TRADING_BUDGET_INR
        )

    preset_cols = st.columns(5)
    preset_amounts = (25_000.0, 50_000.0, 75_000.0, 100_000.0, 200_000.0)
    for col, amount in zip(preset_cols, preset_amounts):
        if amount >= 100_000:
            label = f"₹{int(amount // 100_000)}L"
        else:
            label = f"₹{int(amount // 1000)}K"
        if col.button(label, key=f"rec_sim_preset_{int(amount)}"):
            st.session_state["rec_sim_budget"] = amount
            st.rerun()

    sim_budget = st.number_input(
        "Simulation budget (INR)",
        min_value=1000.0,
        max_value=10_000_000.0,
        step=1000.0,
        key="rec_sim_budget",
        help="Independent from the daily trading budget used for paper trades above.",
    )

    sim_allocation = allocate_budget(
        report,
        sim_budget,
        tier_budget_split_pct=tier_budget_split_pct,
    )

    skipped_invalid = getattr(sim_allocation, "skipped_invalid", None) or []
    backfilled_symbols = getattr(sim_allocation, "backfilled_symbols", None) or []
    if skipped_invalid:
        st.info(
            "Skipped invalid brackets (target ≤ entry): "
            + ", ".join(skipped_invalid)
            + ". Alternates from the same tier were used when available."
        )
    if backfilled_symbols:
        st.caption(
            "Alternate picks used in simulation: " + ", ".join(backfilled_symbols)
        )

    if not sim_allocation.lines:
        st.warning(
            "Simulation budget too small for minimum 1 share in any recommendation — "
            "increase the simulation budget or wait for lower-priced picks."
        )
        return

    total_shares = sum(line.shares for line in sim_allocation.lines)
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Sim budget", format_inr(sim_allocation.budget_inr))
    m2.metric("Would invest", format_inr(sim_allocation.total_invested))
    m3.metric("Total shares", total_shares)
    m4.metric("Expected profit*", format_inr(sim_allocation.expected_profit))
    m5.metric("Cash remaining", format_inr(sim_allocation.cash_remaining))
    st.caption(
        f"Net profit (after tax): {format_inr(sim_allocation.total_net_profit_after_tax)} · "
        f"Max loss if all SL hit: {format_inr(sim_allocation.max_portfolio_loss)}"
    )

    st.dataframe(
        allocation_simulation_dataframe(sim_allocation),
        use_container_width=True,
        hide_index=True,
    )

    with st.expander("Full simulation breakdown"):
        st.dataframe(
            allocation_dataframe(sim_allocation),
            use_container_width=True,
            hide_index=True,
        )

    compare_presets = [25_000.0, 50_000.0, 75_000.0, 100_000.0, 200_000.0]
    compare_budgets = sorted({*compare_presets, float(sim_budget)})
    with st.expander("Compare share counts across budgets"):
        st.caption(
            "Shares per stock at common budget levels — same picks and tier split as above."
        )
        compare_df = budget_simulation_comparison_dataframe(
            report,
            compare_budgets,
            tier_budget_split_pct=tier_budget_split_pct,
        )
        if compare_df.empty:
            st.info("No budgets to compare.")
        else:
            st.dataframe(compare_df, use_container_width=True, hide_index=True)


def _recommendation_tier_options(report) -> list[tuple[str, list]]:
    options: list[tuple[str, list]] = []
    for tier in ("Large Cap", "Mid Cap", "Small Cap"):
        tier_recs = [r for r in report.recommendations if r.cap_tier == tier]
        if tier_recs:
            options.append((tier, tier_recs))
    for label, bucket_recs in report.price_bucket_recommendations.items():
        if bucket_recs:
            options.append((label, bucket_recs))
    return options


def _render_recommendations_report_header(report) -> None:
    if st.session_state.get("rec_from_cache"):
        from app.services.recommendation_engine import apply_price_bucket_sanitize

        apply_price_bucket_sanitize(report)
        cached_at = st.session_state.get("rec_cached_at")
        when = (
            cached_at.strftime("%d %b %Y %H:%M")
            if hasattr(cached_at, "strftime")
            else "earlier today"
        )
        st.caption(f"Loaded from today's saved analysis ({when})")
        short_buckets = [
            label
            for label, recs in report.price_bucket_recommendations.items()
            if 0 < len(recs) < 3
        ]
        if short_buckets:
            st.warning(
                "Saved analysis has fewer than 3 picks in some price buckets "
                f"({', '.join(short_buckets)}). Click **Analyze** to regenerate with "
                "unique stocks per section."
            )
    st.info(
        f"**Data through:** {report.data_through_date.strftime('%d %b %Y')} · "
        f"**Predict for:** {report.prediction_date.strftime('%d %b %Y')} · "
        f"**Lookback:** {report.lookback_days} days · **Eval window:** {report.eval_days} days · "
        f"**Model target cap:** {report.max_target_profit_pct:g}%"
    )
    if report.notes:
        with st.expander("How recommendations work", expanded=False):
            for note in report.notes:
                st.caption(f"ℹ️ {note}")


def _render_recommendations_picks_section(report) -> None:
    st.markdown(
        f"**Top 3 patterns ({report.eval_days}-day performance on scanned universe)**"
    )
    st.dataframe(patterns_dataframe(report), use_container_width=True, hide_index=True)

    rec_df = report_recommendations_dataframe(report)
    tier_options = _recommendation_tier_options(report)

    if rec_df.empty and not tier_options:
        st.warning("No bullish recommendations met the confidence threshold for tomorrow.")
        return

    if not tier_options:
        return

    st.markdown("---")
    tier_labels = [label for label, _ in tier_options]
    selected_tier = st.radio(
        "Recommendation group",
        tier_labels,
        horizontal=True,
        key="rec_tier_view",
        label_visibility="collapsed",
    )
    selected_recs = next(recs for label, recs in tier_options if label == selected_tier)
    group_kind = "market cap" if selected_tier in ("Large Cap", "Mid Cap", "Small Cap") else "price bucket"
    st.markdown(f"**{selected_tier}** ({len(selected_recs)} picks · {group_kind})")
    _render_recommendation_tier_table(selected_recs, report, selected_tier)

    chart_symbol = st.session_state.get("rec_chart_symbol")
    if chart_symbol:
        st.markdown("---")
        _render_recommendation_chart_panel(report, chart_symbol)


def _ensure_recommendation_allocation(report):
    from app.defaults import DEFAULT_DAILY_TRADING_BUDGET_INR
    from app.services.budget_allocator import allocate_budget
    from app.services.recommendation_engine import apply_price_bucket_sanitize, universe_config

    apply_price_bucket_sanitize(report)
    rec_budget = st.session_state.get("rec_budget", DEFAULT_DAILY_TRADING_BUDGET_INR)
    cfg = universe_config()
    allocation = allocate_budget(
        report,
        rec_budget,
        tier_budget_split_pct=cfg.get("tier_budget_split_pct", 33.33),
    )
    st.session_state["rec_allocation"] = allocation
    return allocation


def _render_recommendations_budget_orders_section(report, allocation, budget: float) -> None:
    st.subheader("Budget allocation")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Budget", format_inr(allocation.budget_inr))
    c2.metric("Invested", format_inr(allocation.total_invested))
    c3.metric("Net profit (after tax)", format_inr(allocation.total_net_profit_after_tax))
    c4.metric(
        "Expected profit*",
        format_inr(allocation.expected_profit),
        f"{allocation.expected_return_pct}%",
    )
    c5.metric("Max loss (if all SL hit)", format_inr(allocation.max_portfolio_loss))
    st.caption(
        f"Cash remaining: {format_inr(allocation.cash_remaining)} · "
        f"Gross: {format_inr(allocation.total_gross_profit)} · "
        f"Charges: {format_inr(allocation.total_charges)} · "
        f"STCG: {format_inr(allocation.total_stcg_tax)} · "
        f"*Expected = net profit × pattern hit rate"
    )

    from app.services.budget_allocator import (
        allocation_trading_blocked,
        is_profitable_allocation_line,
    )

    blocked, block_reason = allocation_trading_blocked(allocation)
    if blocked and block_reason:
        st.error(block_reason)

    skipped_unprofitable = getattr(allocation, "skipped_unprofitable", None) or []
    if skipped_unprofitable:
        st.caption(
            "Skipped picks with negative net profit after charges: "
            + ", ".join(skipped_unprofitable)
        )

    alloc_df = allocation_dataframe(allocation)
    if not allocation.lines:
        if not alloc_df.empty:
            st.dataframe(alloc_df, use_container_width=True, hide_index=True)
        else:
            st.warning(
                "Budget too small for minimum 1 share in any recommendation — "
                "increase budget or wait for lower-priced picks."
            )
        return

    from app.services.bracket_utils import is_valid_bracket_levels

    line_symbols = [line.symbol for line in allocation.lines]
    placed_symbols, plan_status = run_async(
        _load_allocation_trade_plan_state(report.prediction_date, line_symbols)
    )
    pending_symbols = [
        line.symbol for line in allocation.lines if line.symbol not in placed_symbols
    ]
    placeable_symbols = [
        line.symbol
        for line in allocation.lines
        if line.symbol in pending_symbols
        and is_valid_bracket_levels(
            line.buy_price, line.actual_sell_price, line.stop_loss
        )
        and is_profitable_allocation_line(line)
        and not blocked
    ]

    skipped_invalid = getattr(allocation, "skipped_invalid", None) or []
    backfilled_symbols = getattr(allocation, "backfilled_symbols", None) or []

    if skipped_invalid:
        st.info(
            "Skipped invalid brackets (target ≤ entry): "
            + ", ".join(skipped_invalid)
            + ". Alternates from the same tier were used when available."
        )
    if backfilled_symbols:
        st.caption(
            "Alternate picks used in allocation: " + ", ".join(backfilled_symbols)
        )

    st.markdown(
        "**Place paper trades** — limit BUY at recommended price; "
        "auto-sell at target or stop loss when price is hit (EOD or live)."
    )
    if pending_symbols:
        place_all_col, _ = st.columns([1, 3])
        with place_all_col:
            if placeable_symbols:
                if st.button(
                    "Place order for all",
                    type="primary",
                    key="rec_place_all",
                ):
                    try:
                        results = run_async(
                            _place_all_allocation_orders(
                                allocation,
                                report.prediction_date,
                                budget,
                                symbols=placeable_symbols,
                            )
                        )
                        placed_n = sum(
                            1 for _, status, _ in results if status == "placed"
                        )
                        errors = [
                            (sym, msg)
                            for sym, status, msg in results
                            if status == "error"
                        ]
                        st.session_state["rec_place_all_notice"] = (
                            f"Placed {placed_n} bracket order(s)."
                            + (f" {len(errors)} failed." if errors else "")
                        )
                        if errors:
                            st.session_state["rec_place_all_errors"] = errors
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))
            else:
                st.button(
                    "Place order for all",
                    type="primary",
                    key="rec_place_all",
                    disabled=True,
                    help=(
                        block_reason
                        if blocked
                        else "No pending lines with valid target above entry and stop below entry."
                    ),
                )
                st.caption(
                    block_reason
                    if blocked
                    else "No placeable orders — refresh analysis or fix invalid brackets."
                )
    else:
        st.caption("All allocation lines already have trade plans for this date.")

    if notice := st.session_state.pop("rec_place_all_notice", None):
        st.success(notice)
    if errors := st.session_state.pop("rec_place_all_errors", None):
        for sym, msg in errors[:5]:
            st.warning(f"{sym}: {msg}")

    hdr = st.columns([0.95, 0.8, 0.5, 0.8, 0.8, 0.85, 0.8, 1.05, 0.7])
    hdr[0].markdown("**Stock**")
    hdr[1].markdown("**Tier**")
    hdr[2].markdown("**Shares**")
    hdr[3].markdown("**Buy ref.**")
    hdr[4].markdown("**Stop loss**")
    hdr[5].markdown("**Sell target**")
    hdr[6].markdown("**Investment**")
    hdr[7].markdown("**Pattern**")
    hdr[8].markdown("**Status**")
    st.caption(
        "Sell target: price to exit at your threshold (full model projection in parentheses). "
        "Orders auto-sell at the first price."
    )

    for idx, row in enumerate(allocation_summary_rows(allocation)):
        line = row["line"]
        cols = st.columns([0.95, 0.8, 0.5, 0.8, 0.8, 0.85, 0.8, 1.05, 0.7])
        cols[0].write(f"**{line.symbol}**")
        cols[1].write(line.cap_tier)
        cols[2].write(str(line.shares))
        cols[3].write(format_inr(line.buy_price))
        cols[4].write(format_inr(line.stop_loss))
        cols[5].write(
            format_sell_target_display(
                line.actual_sell_price,
                line.model_target_price,
            )
        )
        cols[6].write(format_inr(line.investment))
        cols[7].write(line.pattern_name)
        with cols[8]:
            if line.symbol in placed_symbols:
                st.button(
                    "Order placed",
                    key=f"rec_placed_{idx}_{line.symbol}",
                    disabled=True,
                )
                detail = plan_status.get(line.symbol)
                if detail and detail != "Cancelled":
                    st.caption(detail)
            elif not is_valid_bracket_levels(
                line.buy_price, line.actual_sell_price, line.stop_loss
            ):
                st.button(
                    "Invalid bracket",
                    key=f"rec_invalid_{idx}_{line.symbol}",
                    disabled=True,
                    help=(
                        f"Target must be above entry (₹{line.buy_price:.2f}); "
                        f"stop must be below. Re-run analysis to refresh levels."
                    ),
                )
                st.caption("Target ≤ entry — skip or refresh analysis")
            elif blocked or not is_profitable_allocation_line(line):
                st.button(
                    "Unprofitable",
                    key=f"rec_unprof_{idx}_{line.symbol}",
                    disabled=True,
                    help=(
                        f"Net after charges: {format_inr(line.net_profit_after_tax)} "
                        f"if target hits — skip this pick."
                    ),
                )
                st.caption("Charges exceed target upside")
            elif st.button(
                "Place trade",
                key=f"rec_place_{idx}_{line.symbol}",
                type="secondary",
            ):
                try:
                    order = run_async(
                        _place_allocation_buy(
                            line.symbol,
                            line.shares,
                            budget,
                            recommendation_date=report.prediction_date,
                            buy_price=line.buy_price,
                            stop_loss=line.stop_loss,
                            target_price=line.actual_sell_price,
                            pattern_name=line.pattern_name,
                        )
                    )
                    if order.status.value == "FILLED":
                        st.success(
                            f"Bought {line.shares} × {line.symbol} @ "
                            f"{format_inr(order.filled_price)}"
                        )
                    elif order.status.value == "PENDING":
                        st.success(
                            f"Limit BUY placed for {line.shares} × {line.symbol} @ "
                            f"{format_inr(line.buy_price)} — fills when price reaches entry; "
                            f"target {format_inr(line.actual_sell_price)}, "
                            f"SL {format_inr(line.stop_loss)}"
                        )
                    elif order.status.value == "REJECTED":
                        st.error(
                            f"Order rejected for {line.symbol} — "
                            "check budget remaining or market data."
                        )
                    else:
                        st.info(f"Order {order.status.value} for {line.symbol}")
                    st.cache_data.clear()
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

    with st.expander("Full allocation breakdown"):
        st.dataframe(alloc_df, use_container_width=True, hide_index=True)


def _render_midday_place_orders_section(
    report,
    allocation,
    comparison_rows,
    *,
    budget: float,
    budget_ctx,
) -> None:
    from app.services.bracket_utils import is_valid_bracket_levels
    from app.services.midday_recommendations import MiddayActionKind

    applied_symbols, plan_status = run_async(
        _load_midday_place_state(report.prediction_date, allocation)
    )

    st.markdown("---")
    st.markdown("**Mid-day budget allocation**")
    a1, a2, a3, a4, a5 = st.columns(5)
    a1.metric("Budget", format_inr(allocation.budget_inr))
    a2.metric("Invested", format_inr(allocation.total_invested))
    a3.metric("Net profit (after tax)", format_inr(allocation.total_net_profit_after_tax))
    a4.metric(
        "Expected profit*",
        format_inr(allocation.expected_profit),
        f"{allocation.expected_return_pct}%",
    )
    a5.metric("Max loss (if all SL hit)", format_inr(allocation.max_portfolio_loss))
    st.caption(
        f"Cash remaining: {format_inr(allocation.cash_remaining)} · "
        f"Available base budget now: {format_inr(budget_ctx.available_inr)} · "
        f"Gross: {format_inr(allocation.total_gross_profit)} · "
        f"Charges: {format_inr(allocation.total_charges)} · "
        f"STCG: {format_inr(allocation.total_stcg_tax)} · "
        f"*Expected = net profit × pattern hit rate"
    )

    from app.services.budget_allocator import (
        allocation_trading_blocked,
        is_profitable_allocation_line,
    )

    blocked, block_reason = allocation_trading_blocked(allocation)
    if blocked and block_reason:
        st.error(block_reason)

    st.markdown("**Place order** (calibrations apply only when you click)")
    line_by_symbol = {line.symbol.upper(): line for line in allocation.lines}
    closed_statuses = {"Target hit", "Stop hit", "3:25 PM exit"}
    pending_rows = [
        row
        for row in comparison_rows
        if row.symbol.upper() not in applied_symbols
        and (row.plan_status or "") not in closed_statuses
    ]
    placeable_symbols = [
        row.symbol
        for row in pending_rows
        if is_valid_bracket_levels(
            line_by_symbol[row.symbol.upper()].buy_price,
            line_by_symbol[row.symbol.upper()].actual_sell_price,
            line_by_symbol[row.symbol.upper()].stop_loss,
        )
        and is_profitable_allocation_line(line_by_symbol[row.symbol.upper()])
        and not blocked
    ]

    if pending_rows:
        place_all_col, _ = st.columns([1, 3])
        with place_all_col:
            if placeable_symbols:
                if st.button(
                    "Place order for all",
                    type="primary",
                    key="midday_place_all",
                ):
                    try:
                        results = run_async(
                            _place_all_midday_orders(
                                allocation,
                                report.prediction_date,
                                budget,
                                symbols=placeable_symbols,
                                morning_budget_inr=budget_ctx.morning_budget_inr,
                                session_realized_pnl=budget_ctx.session_realized_pnl,
                            )
                        )
                        placed_n = sum(1 for _, status, _ in results if status == "placed")
                        errors = [
                            (sym, msg) for sym, status, msg in results if status == "error"
                        ]
                        st.session_state["midday_place_all_notice"] = (
                            f"Applied {placed_n} mid-day order(s)."
                            + (f" {len(errors)} failed." if errors else "")
                        )
                        if errors:
                            st.session_state["midday_place_all_errors"] = errors
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))
            else:
                st.button(
                    "Place order for all",
                    type="primary",
                    key="midday_place_all",
                    disabled=True,
                    help=(
                        block_reason
                        if blocked
                        else "No pending lines with valid target above entry and stop below entry."
                    ),
                )
                st.caption(
                    block_reason
                    if blocked
                    else "No placeable orders — refresh analysis or fix invalid brackets."
                )
    else:
        st.caption("All mid-day lines already have orders or calibrations applied.")

    if notice := st.session_state.pop("midday_place_all_notice", None):
        st.success(notice)
    if errors := st.session_state.pop("midday_place_all_errors", None):
        for sym, msg in errors[:5]:
            st.warning(f"{sym}: {msg}")

    hdr = st.columns([1.0, 0.9, 0.55, 0.95, 0.95, 0.95, 0.95, 0.9, 1.2])
    hdr[0].markdown("**Stock**")
    hdr[1].markdown("**Status**")
    hdr[2].markdown("**Shares**")
    hdr[3].markdown("**Buy**")
    hdr[4].markdown("**Target**")
    hdr[5].markdown("**Stop**")
    hdr[6].markdown("**Investment**")
    hdr[7].markdown("**Changes**")
    hdr[8].markdown("**Action**")

    for idx, row in enumerate(comparison_rows):
        line = line_by_symbol[row.symbol.upper()]
        cols = st.columns([1.0, 0.9, 0.55, 0.95, 0.95, 0.95, 0.95, 0.9, 1.2])
        cols[0].write(f"**{row.symbol}**")
        cols[1].write(row.plan_status or "—")
        cols[2].write(str(row.shares))
        cols[3].write(format_inr(row.midday_buy))
        cols[4].write(format_inr(row.midday_target))
        cols[5].write(format_inr(row.midday_stop))
        cols[6].write(format_inr(line.investment))
        change_bits = []
        if row.buy_changed:
            change_bits.append("buy")
        if row.target_changed:
            change_bits.append("target")
        if row.stop_changed:
            change_bits.append("stop")
        cols[7].write(", ".join(change_bits) if change_bits else "—")

        with cols[8]:
            if row.symbol.upper() in applied_symbols:
                st.button(
                    "Order placed",
                    key=f"midday_placed_{idx}_{row.symbol}",
                    disabled=True,
                )
                detail = plan_status.get(row.symbol)
                if detail and detail != "Cancelled":
                    st.caption(detail)
            elif row.plan_status in closed_statuses:
                st.button(
                    "Closed",
                    key=f"midday_closed_{idx}_{row.symbol}",
                    disabled=True,
                )
            elif not is_valid_bracket_levels(
                line.buy_price, line.actual_sell_price, line.stop_loss
            ):
                st.button(
                    "Invalid bracket",
                    key=f"midday_invalid_{idx}_{row.symbol}",
                    disabled=True,
                )
            elif st.button(
                "Place order",
                key=f"midday_place_{idx}_{row.symbol}",
                type="secondary",
            ):
                try:
                    order = run_async(
                        _place_midday_allocation_buy(
                            line.symbol,
                            line.shares,
                            budget,
                            recommendation_date=report.prediction_date,
                            buy_price=line.buy_price,
                            stop_loss=line.stop_loss,
                            target_price=line.actual_sell_price,
                            pattern_name=line.pattern_name,
                            morning_budget_inr=budget_ctx.morning_budget_inr,
                            session_realized_pnl=budget_ctx.session_realized_pnl,
                        )
                    )
                    if row.action == MiddayActionKind.NEW:
                        action_msg = "placed"
                    elif row.action == MiddayActionKind.PENDING_CALIBRATE:
                        action_msg = "pending order calibrated"
                    else:
                        action_msg = "open position targets calibrated"

                    if order.status.value == "FILLED":
                        st.success(
                            f"{row.symbol}: {action_msg} — bought {line.shares} @ "
                            f"{format_inr(order.filled_price)}"
                        )
                    elif order.status.value == "PENDING":
                        st.success(
                            f"{row.symbol}: {action_msg} — limit BUY @ "
                            f"{format_inr(line.buy_price)}"
                        )
                    else:
                        st.info(f"{row.symbol}: {action_msg} ({order.status.value})")
                    st.cache_data.clear()
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))


def _sync_max_target_session_key(session_key: str) -> float:
    """Default model target from recommendation_universe.json (not .env 80%)."""
    from app.config import settings
    from app.defaults import DEFAULT_MAX_TARGET_PROFIT_PCT
    from app.services.recommendation_engine import universe_default_max_target_profit_pct

    cfg_default = universe_default_max_target_profit_pct()
    legacy = float(getattr(settings, "max_target_profit_pct", DEFAULT_MAX_TARGET_PROFIT_PCT))
    current = st.session_state.get(session_key)
    if current is None or (current == legacy and cfg_default != legacy):
        st.session_state[session_key] = cfg_default
    return float(st.session_state[session_key])


def _render_recommendations_body(budget: float, default_budget: float) -> None:
    """Progress, cached picks, and report sections (safe to refresh from a fragment)."""
    if st.session_state.get("rec_last_error"):
        st.error(st.session_state.pop("rec_last_error"))
    if st.session_state.get("rec_last_success"):
        st.success(st.session_state.pop("rec_last_success"))

    if is_kind_running(JobKind.RECOMMENDATIONS):
        import time as _time

        from ui.background_jobs import _format_elapsed

        for job in list_jobs():
            if job["kind"] == JobKind.RECOMMENDATIONS.value:
                elapsed = _time.time() - float(job.get("started_at", _time.time()))
                stale = _time.time() - float(
                    job.get("updated_at", job.get("started_at", _time.time()))
                )
                msg = job.get("message", "Analyzing…")
                st.info(f"Running in background: {msg} ({_format_elapsed(elapsed)})")
                st.progress(job.get("progress", 0.0), text=msg)
                if stale > 20:
                    st.warning(
                        "Taking longer than usual — if this persists, refresh the page; "
                        "results may already be saved."
                    )
                st.caption("Analysis runs in the background — switch tabs freely.")
                break
        return

    _ensure_recommendation_session_state()

    if st.session_state.get("rec_stale_session") and st.session_state.get("rec_report"):
        from app.services.market_calendar import active_market_session_date

        active = active_market_session_date()
        rec = st.session_state["rec_report"]
        st.warning(
            f"Showing saved picks for **{rec.prediction_date.strftime('%d %b %Y')}**. "
            f"Run analysis again for **{active.strftime('%d %b %Y')}**."
        )

    report = st.session_state.get("rec_report")

    if not report:
        from app.services.recommendation_engine import universe_config

        rec_cfg = universe_config()
        eval_days = int(rec_cfg.get("eval_days", 30))
        lookback_days = int(rec_cfg.get("lookback_days", 30))
        st.info(
            "Click **Run recommendation analysis** to scan all NIFTY250 stocks, "
            f"rank patterns on the last {eval_days} trading days, and build tomorrow's trade plan."
        )
        with st.expander("How it works"):
            st.markdown(
                f"""
                1. **{eval_days}-day backtest** on full NIFTY250 ({lookback_days}-day lookback · local market-data table)
                2. **Top 3 patterns** ranked by hit rate (minimum 55% success); expands to patterns 4+ if a tier needs more picks
                3. **Cap tier picks** — at least 3 BUY ideas each for Large / Mid / Small cap (grouped by latest price: ≥ ₹100 / ≥ ₹30 / ≥ ₹10)
                4. **Price buckets** — at least 3 picks each for stocks below ₹100, ₹500, and ₹1000
                5. **Trade levels** — buy reference, stop loss (support/ATR), resistance, target
                6. **Budget allocator** — splits your INR budget by confidence, integer share counts, expected profit
                """
            )
        return

    st.markdown("---")
    st.subheader("Recommendations for tomorrow")
    _render_recommendations_report_header(report)

    rec_section = st.radio(
        "Recommendation section",
        ["Stock picks", "Budget & orders", "Budget simulation"],
        horizontal=True,
        key="rec_page_section",
        label_visibility="collapsed",
    )

    if rec_section == "Stock picks":
        _render_recommendations_picks_section(report)
    elif rec_section == "Budget & orders":
        allocation = _ensure_recommendation_allocation(report)
        st.markdown("---")
        _render_recommendations_budget_orders_section(report, allocation, budget)
    else:
        from app.services.recommendation_engine import universe_config

        _sim_cfg = universe_config()
        _sim_budget = float(st.session_state.get("rec_budget", default_budget))
        _render_budget_simulation_section(
            report,
            tier_budget_split_pct=_sim_cfg.get("tier_budget_split_pct", 33.33),
            trading_budget_inr=_sim_budget,
        )


@st.fragment(run_every=timedelta(seconds=1))
def _recommendations_live_fragment(budget: float, default_budget: float) -> None:
    """Poll job state without full-page reruns (avoids blank screen)."""
    sync_jobs_to_session()
    if not is_kind_running(JobKind.RECOMMENDATIONS):
        st.session_state.pop("_rec_live_poll", None)
    _render_recommendations_body(budget, default_budget)


def render_recommendations_page():
    from ui.streamlit_imports import ensure_applicable_rates_fresh, ensure_trade_tax_fresh

    ensure_applicable_rates_fresh()
    ensure_trade_tax_fresh()

    from app.config import settings
    from app.defaults import DEFAULT_DAILY_TRADING_BUDGET_INR
    from app.services.recommendation_engine import universe_config

    rec_cfg = universe_config()
    eval_days = int(rec_cfg.get("eval_days", 30))
    lookback_days = int(rec_cfg.get("lookback_days", 30))

    st.subheader("Recommendation Engine")
    st.caption(
        "Scans full NIFTY250 · large / mid / small cap by latest price · at least 3 picks per tier · "
        f"plus price buckets (below ₹100 / ₹500 / ₹1000) · {lookback_days}-day local market data"
    )

    default_budget = float(
        getattr(settings, "daily_trading_budget_inr", DEFAULT_DAILY_TRADING_BUDGET_INR)
    )
    default_max_target = _sync_max_target_session_key("rec_max_target_pct")
    cfg_col1, cfg_col2 = st.columns(2)
    with cfg_col1:
        budget = st.number_input(
            "Daily trading budget (INR)",
            min_value=1000.0,
            max_value=10_000_000.0,
            value=st.session_state.get("rec_budget", default_budget),
            step=1000.0,
            help="Total capital available for today's recommended trades. Configurable via DAILY_TRADING_BUDGET_INR in .env",
        )
    with cfg_col2:
        max_target_pct = st.number_input(
            "Model target max (%)",
            min_value=1.0,
            max_value=100.0,
            value=float(st.session_state.get("rec_max_target_pct", default_max_target)),
            step=1.0,
            help=(
                "Cap model sell target as % above buy price. "
                f"Default {default_max_target:g}% from recommendation_universe.json "
                "(tighter for same-day square-off)."
            ),
        )
    st.session_state["rec_budget"] = budget
    st.session_state["rec_max_target_pct"] = max_target_pct

    run_clicked = st.button(
        "Run recommendation analysis",
        type="primary",
        disabled=is_kind_running(JobKind.RECOMMENDATIONS),
    )

    if run_clicked:
        if is_any_job_running() and not is_kind_running(JobKind.RECOMMENDATIONS):
            st.warning("Wait for the current background task to finish.")
        elif start_recommendations_job(budget, max_target_profit_pct=max_target_pct):
            st.session_state["_rec_live_poll"] = True

    if is_kind_running(JobKind.RECOMMENDATIONS) or st.session_state.get("_rec_live_poll"):
        _recommendations_live_fragment(budget, default_budget)
    else:
        _render_recommendations_body(budget, default_budget)


def _render_midday_recommendations_body(budget_ctx, budget: float) -> None:
    """Progress, cached picks, and report sections (safe to refresh from a fragment)."""
    from app.services.recommendation_cache import load_cached_recommendations_for_ui
    from app.services.midday_recommendations import build_midday_comparison_rows
    from ui.midday_recommendations_display import midday_comparison_dataframe

    if st.session_state.get("midday_last_error"):
        st.error(st.session_state.pop("midday_last_error"))
    if st.session_state.get("midday_last_success"):
        st.success(st.session_state.pop("midday_last_success"))

    if is_kind_running(JobKind.MIDDAY_RECOMMENDATIONS):
        import time as _time

        from ui.background_jobs import _format_elapsed

        for job in list_jobs():
            if job["kind"] == JobKind.MIDDAY_RECOMMENDATIONS.value:
                elapsed = _time.time() - float(job.get("started_at", _time.time()))
                stale = _time.time() - float(
                    job.get("updated_at", job.get("started_at", _time.time()))
                )
                msg = job.get("message", "Analyzing…")
                st.info(f"Running in background: {msg} ({_format_elapsed(elapsed)})")
                st.progress(job.get("progress", 0.0), text=msg)
                if stale > 20:
                    st.warning(
                        "Taking longer than usual — if this persists, refresh the page; "
                        "results may already be saved."
                    )
                st.caption("Analysis runs in the background — switch tabs freely.")
                break
        return

    report = st.session_state.get("midday_report")
    allocation = st.session_state.get("midday_allocation")

    if not report or not allocation:
        morning_cached = run_async(load_cached_recommendations_for_ui())
        if morning_cached is None:
            st.warning(
                "No morning recommendations found. Run **Run recommendation analysis** on the "
                "**Recommendations** tab first."
            )
        else:
            st.info("Click **Run mid-day analysis** to refresh session data and generate updated picks.")
        return

    morning_report = None
    morning_allocation = None
    morning_cached = run_async(load_cached_recommendations_for_ui())
    if morning_cached is not None:
        morning_report, morning_allocation, _, _, _ = morning_cached

    if st.session_state.get("midday_from_cache"):
        cached_at = st.session_state.get("midday_cached_at")
        when = (
            cached_at.strftime("%d %b %Y %H:%M")
            if hasattr(cached_at, "strftime")
            else "earlier today"
        )
        st.caption(f"Loaded from today's saved mid-day analysis ({when})")

    st.markdown("---")
    st.info(
        f"**Data through:** {report.data_through_date.strftime('%d %b %Y')} (partial session) · "
        f"**Predict for:** {report.prediction_date.strftime('%d %b %Y')} · "
        f"**Compared with:** morning snapshot"
    )
    if report.notes:
        with st.expander("How recommendations work", expanded=False):
            for note in report.notes:
                st.caption(f"ℹ️ {note}")

    line_symbols = [line.symbol for line in allocation.lines]
    _, plan_status = run_async(
        _load_allocation_trade_plan_state(report.prediction_date, line_symbols)
    )
    comparison_rows = build_midday_comparison_rows(
        allocation,
        morning_report=morning_report,
        morning_allocation=morning_allocation,
        plan_status_by_symbol=plan_status,
    )
    compare_df = midday_comparison_dataframe(comparison_rows)

    if compare_df.empty:
        st.warning("No budget allocation lines from mid-day analysis.")
        return

    midday_section = st.radio(
        "Mid-day section",
        ["Analysis", "Place orders"],
        horizontal=True,
        key="midday_page_section",
        label_visibility="collapsed",
    )

    if midday_section == "Analysis":
        st.markdown("**Mid-day picks vs morning**")
        st.dataframe(compare_df, use_container_width=True, hide_index=True)
        st.markdown("---")
        st.markdown("**Mid-day budget allocation**")
        a1, a2, a3, a4, a5 = st.columns(5)
        a1.metric("Budget", format_inr(allocation.budget_inr))
        a2.metric("Invested", format_inr(allocation.total_invested))
        a3.metric("Net profit (after tax)", format_inr(allocation.total_net_profit_after_tax))
        a4.metric(
            "Expected profit*",
            format_inr(allocation.expected_profit),
            f"{allocation.expected_return_pct}%",
        )
        a5.metric("Max loss (if all SL hit)", format_inr(allocation.max_portfolio_loss))
        st.caption(
            f"Cash remaining: {format_inr(allocation.cash_remaining)} · "
            f"Available base budget now: {format_inr(budget_ctx.available_inr)} · "
            f"Gross: {format_inr(allocation.total_gross_profit)} · "
            f"Charges: {format_inr(allocation.total_charges)} · "
            f"STCG: {format_inr(allocation.total_stcg_tax)} · "
            f"*Expected = net profit × pattern hit rate"
        )
    else:
        _render_midday_place_orders_section(
            report,
            allocation,
            comparison_rows,
            budget=budget,
            budget_ctx=budget_ctx,
        )


@st.fragment(run_every=timedelta(seconds=1))
def _midday_recommendations_live_fragment(budget_ctx, budget: float) -> None:
    """Poll job state without full-page reruns (avoids blank screen)."""
    sync_jobs_to_session()
    if not is_kind_running(JobKind.MIDDAY_RECOMMENDATIONS):
        st.session_state.pop("_midday_live_poll", None)
    _render_midday_recommendations_body(budget_ctx, budget)


def render_midday_recommendations_page():
    from app.services.market_calendar import is_midday_analysis_ready
    from ui.streamlit_imports import ensure_applicable_rates_fresh, ensure_trade_tax_fresh

    ensure_applicable_rates_fresh()
    ensure_trade_tax_fresh()

    _ensure_midday_session_state()

    st.subheader("Mid day recommendation analysis")
    st.caption(
        "After **11:45 AM IST**, refresh today's session OHLC, rerun the recommendation engine, "
        "and compare picks against this morning's analysis. "
        "**Place order** applies calibrations (pending limits, open targets) — nothing changes until you click."
    )

    midday_ready = is_midday_analysis_ready()
    has_results = bool(
        st.session_state.get("midday_report") and st.session_state.get("midday_allocation")
    )

    if not midday_ready and not has_results:
        st.info(
            "Available on trading days from **11:45 AM to 4:30 PM IST**. "
            "Run the morning analysis on the **Recommendations** tab first."
        )
        return

    default_max_target = _sync_max_target_session_key("midday_max_target_pct")
    budget_ctx = run_async(_midday_budget_context())
    st.session_state["midday_budget"] = budget_ctx.available_inr

    st.markdown("**Budget (base only — profits not reinvested)**")
    b1, b2, b3, b4 = st.columns(4)
    b1.metric("Morning budget", format_inr(budget_ctx.morning_budget_inr))
    b2.metric("Invested (open)", format_inr(budget_ctx.invested_cost))
    b3.metric("Realized P&L (today)", format_inr(budget_ctx.session_realized_pnl))
    b4.metric("Available for mid-day", format_inr(budget_ctx.available_inr))
    st.caption(
        "Available = morning budget − open position cost − today's realized P&L "
        "(profits are withheld from new trades)."
    )

    max_target_pct = st.number_input(
        "Model target max (%)",
        min_value=1.0,
        max_value=100.0,
        value=float(st.session_state.get("midday_max_target_pct", default_max_target)),
        step=1.0,
        key="midday_max_target_input",
    )
    st.session_state["midday_max_target_pct"] = max_target_pct
    budget = budget_ctx.available_inr

    if not midday_ready and has_results:
        st.caption(
            "Showing saved mid-day analysis from earlier today. "
            "Re-run is available from **11:45 AM to 4:30 PM IST** on trading days."
        )

    run_clicked = st.button(
        "Run mid-day analysis",
        type="primary",
        disabled=not midday_ready or is_kind_running(JobKind.MIDDAY_RECOMMENDATIONS),
    )
    if run_clicked:
        if is_any_job_running() and not is_kind_running(JobKind.MIDDAY_RECOMMENDATIONS):
            st.warning("Wait for the current background task to finish.")
        elif start_midday_recommendations_job(budget, max_target_profit_pct=max_target_pct):
            st.session_state["_midday_live_poll"] = True

    if is_kind_running(JobKind.MIDDAY_RECOMMENDATIONS) or st.session_state.get("_midday_live_poll"):
        _midday_recommendations_live_fragment(budget_ctx, budget)
    else:
        _render_midday_recommendations_body(budget_ctx, budget)


def render_eod_analysis_page():
    from app.services.market_calendar import last_completed_trading_day

    st.subheader("Analysis & EOD Report")
    st.caption(
        "End-of-day review for recommendation bracket trades — entry fill rate, target/stop touches, "
        "3:25 PM square-off exits, missed targets, alternative patterns, and reasoning for tomorrow."
    )

    trade_dates = run_async(_list_eod_trade_dates())
    default_date = last_completed_trading_day()
    if trade_dates:
        default_date = trade_dates[0]

    date_options = trade_dates or [default_date]
    selected = st.selectbox(
        "Trade date (recommendation batch)",
        options=date_options,
        format_func=lambda d: d.strftime("%d %b %Y"),
        index=0,
        key="eod_trade_date",
    )

    refresh = st.button("Refresh EOD analysis", type="primary", key="eod_refresh")
    cache_key = f"eod_report_{selected.isoformat()}"
    if refresh:
        try:
            st.session_state[cache_key] = run_async(_run_eod_trade_analysis(selected))
        except Exception as exc:
            st.error(str(exc))
            return

    report = st.session_state.get(cache_key)
    if report is None:
        st.info("Select a trade date and click **Refresh EOD analysis**.")
        return

    if report.total_plans == 0:
        st.warning(
            f"No bracket orders found for **{selected.strftime('%d %b %Y')}**. "
            "Place trades from the Recommendations tab first."
        )
        return

    st.markdown("---")
    st.markdown(f"**As of:** {report.as_of_date.strftime('%d %b %Y')}")

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Total orders", report.total_plans)
    m2.metric("Entries made", report.entries_made)
    m3.metric("Touched stop loss", report.touched_stop_loss)
    m4.metric("Touched target", report.touched_target)
    m5.metric("Closed above target", report.closed_above_target)
    m6.metric("Closed below target", report.closed_below_target)

    n1, n2, n3, n4, n5, n6 = st.columns(6)
    n1.metric("Target exits", report.target_hit_exits)
    n2.metric("Stop exits", report.stop_hit_exits)
    n3.metric("3:25 PM exits", report.time_exit_exits)
    n4.metric("Missed target", report.missed_target_trades)
    n5.metric(
        "Square-off missed",
        report.square_off_missed_targets,
        help="Positions sold at 3:25 PM that had not reached target",
    )
    n6.metric("Day realized P&L", format_inr(report.day_realized_pnl))

    st.markdown("---")
    st.markdown("**Trade breakdown**")
    trade_df = trade_analysis_dataframe(report)
    st.dataframe(trade_df, use_container_width=True, hide_index=True)

    missed_df = missed_target_dataframe(report)
    if not missed_df.empty:
        st.markdown("**Executed trades that missed target**")
        st.caption(
            f"{report.missed_target_trades} position(s) did not reach target"
            + (
                f" ({report.square_off_missed_targets} via 3:25 PM square-off)"
                if report.square_off_missed_targets
                else ""
            )
            + (
                f" · avg miss {report.avg_target_miss_pct:.2f}% below target"
                if report.avg_target_miss_pct is not None
                else ""
            )
        )
        st.dataframe(missed_df, use_container_width=True, hide_index=True)

    alt_df = better_patterns_dataframe(report)
    if not alt_df.empty:
        st.markdown("**Alternative patterns — more profitable today**")
        st.caption(
            "Other bullish patterns that predicted correctly and would have yielded higher P&L "
            "at the same entry price."
        )
        st.dataframe(alt_df, use_container_width=True, hide_index=True)
    else:
        st.caption("No alternative patterns beat today's exits on entered positions.")

    executed_df = executed_trade_reviews_dataframe(report)
    st.markdown("---")
    st.subheader("Trades taken — peak vs exit")
    st.caption(
        "For executed positions: intraday peak, actual sell price, pattern target, and how much "
        "additional profit was available at the high. Available after **3:45 PM IST** for today's session."
    )
    if not report.post_session_ready:
        st.info(report.post_session_status or "Post-session trade review is not ready yet.")
    elif not executed_df.empty:
        st.dataframe(executed_df, use_container_width=True, hide_index=True)
        if report.executed_trade_lessons:
            st.markdown("**Lessons learnt**")
            for lesson in report.executed_trade_lessons:
                st.markdown(f"- {lesson}")
    else:
        st.info(
            "No executed trades with complete intraday OHLC to review for this batch. "
            "Today's candle data may not be synced yet — use **Refresh market data** "
            "(auto-runs at **3:45 PM** and **6:00 PM IST**)."
        )

    missed_profit_df = missed_profitable_trades_dataframe(report)
    st.markdown("---")
    st.subheader("NIFTY250 — profitable closes not recommended")
    st.caption(
        "NIFTY250 stocks that finished the day up but were **not** in our recommendation picks — "
        "mirrors the profitable rows in Trading → NIFTY250. Shows why top patterns missed them "
        "and which pattern would have caught the move. Available after **3:45 PM IST**."
    )
    if not report.post_session_ready:
        st.info(report.post_session_status or "Post-session analysis is not ready yet.")
    elif not missed_profit_df.empty:
        st.dataframe(missed_profit_df, use_container_width=True, hide_index=True)
        if report.missed_profitable_lessons:
            st.markdown("**Lessons learnt**")
            for lesson in report.missed_profitable_lessons:
                st.markdown(f"- {lesson}")
    else:
        st.info(
            "No missed movers found — either all profitable universe stocks were recommended, "
            "no symbol cleared the minimum day-return threshold, or **today's OHLCV is not synced yet**. "
            "Use **Refresh market data** (auto-runs at **3:45 PM** and **6:00 PM IST**)."
        )

    st.markdown("---")
    st.subheader("Reasoning engine")
    st.caption("Rule-based analysis of what worked, what was missed, and suggested actions for tomorrow.")

    if report.insights:
        for insight in report.insights:
            with st.expander(f"{insight.category.title()} · {insight.title}", expanded=insight.priority <= 2):
                st.write(insight.detail)
    else:
        st.info("No insights generated for this batch.")

    if report.tomorrow_actions:
        st.markdown("**Suggested actions for tomorrow**")
        for action in report.tomorrow_actions:
            st.markdown(f"- {action}")


def render_paper_trading_trend_page():
    from app.defaults import DEFAULT_PAPER_TRADING_RETENTION_DAYS

    st.subheader("Paper trading performance")
    _render_paper_trading_after_tax_section()
    st.divider()

    refresh = st.button("Refresh trend", type="primary", key="trend_refresh")
    cache_key = "paper_trading_trend_report"
    if refresh or cache_key not in st.session_state:
        try:
            st.session_state[cache_key] = run_async(_load_paper_trading_trend())
        except Exception as exc:
            st.error(str(exc))
            return

    report = st.session_state.get(cache_key)
    if report is None:
        return

    window_days = getattr(report, "window_days", DEFAULT_PAPER_TRADING_RETENTION_DAYS)
    window_start = getattr(report, "window_start", report.as_of)
    st.caption(
        f"Rolling **{window_days}-day** window "
        f"({window_start.strftime('%d %b %Y')} – {report.as_of.strftime('%d %b %Y')}) · "
        "realized P&L by day from bracket and manual trades — cumulative trend, win rate, "
        "and pattern breakdown."
    )

    if report.total_closed_trades == 0:
        st.warning(
            "No closed trades yet in this window. Place bracket orders from **Recommendations** "
            "or trades from **Trading** — charts fill in once positions close. "
            "If you had history before tonight's database reset, restore a `pg_dump` backup."
        )

    profitable_pct = (
        round(report.profitable_days / report.trading_days * 100, 1)
        if report.trading_days
        else 0.0
    )

    st.markdown("### Performance summary")
    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric(
        "Window return",
        format_pct(report.total_return_pct),
        help=f"Realized P&L in last {report.window_days} days vs initial capital",
    )
    k2.metric("Window realized P&L", format_inr(report.total_realized_pnl))
    k3.metric("Portfolio value", format_inr(report.total_value))
    k4.metric("Win rate", f"{report.overall_win_rate_pct:.1f}%")
    k5.metric("Profitable days", f"{report.profitable_days}/{report.trading_days}")
    k6.metric("Closed trades", report.total_closed_trades)

    h1, h2, h3 = st.columns(3)
    h1.metric("Best day", format_inr(report.best_day_pnl))
    h2.metric("Worst day", format_inr(report.worst_day_pnl))
    h3.metric("Profitable day rate", f"{profitable_pct:.1f}%")

    if report.unrealized_pnl != 0 or report.open_positions:
        st.caption(
            f"Open positions: **{report.open_positions}** · "
            f"Unrealized P&L: **{format_inr(report.unrealized_pnl)}** · "
            f"Cash: **{format_inr(report.cash_balance)}** · "
            f"Equity: **{format_inr(report.equity_value)}**"
        )

    cumulative_fig, daily_fig = build_trend_charts(report)
    win_fig = build_win_rate_chart(report)
    c1, c2 = st.columns(2)
    with c1:
        st.plotly_chart(cumulative_fig, use_container_width=True)
    with c2:
        st.plotly_chart(daily_fig, use_container_width=True)
    st.plotly_chart(win_fig, use_container_width=True)

    st.markdown("---")
    st.markdown("### Daily results")
    daily_df = daily_trend_dataframe(report)
    st.dataframe(
        daily_df,
        use_container_width=True,
        hide_index=True,
        column_config=daily_trend_column_config(),
    )

    pattern_df = pattern_trend_dataframe(report)
    if not pattern_df.empty:
        st.markdown("### Performance by pattern")
        st.dataframe(pattern_df, use_container_width=True, hide_index=True)

    st.markdown("### Trades by day")
    if not report.trades_by_day:
        st.caption("No closed trades in this window yet.")
    for day in sorted(report.trades_by_day.keys(), reverse=True):
        day_trades = report.trades_by_day[day]
        day_pnl = round(sum(t.realized_pnl for t in day_trades), 2)
        label = day.strftime("%d %b %Y")
        with st.expander(f"{label} · {len(day_trades)} trade(s) · P&L {format_inr(day_pnl)}"):
            st.dataframe(
                closed_trades_dataframe(day_trades),
                use_container_width=True,
                hide_index=True,
            )


@st.fragment(run_every=timedelta(seconds=60))
def _scheduled_market_sync_tick(db_ready: bool) -> None:
    if not db_ready:
        return
    from ui.scheduled_market_sync import try_start_scheduled_market_sync

    slot = try_start_scheduled_market_sync()
    if slot:
        # Keep Trading page watching progress without hiding the page body.
        st.session_state["_market_sync_live_poll"] = True
        st.session_state["last_job_notice"] = (
            f"Scheduled market data sync started ({slot} IST)."
        )


@st.fragment(run_every=timedelta(seconds=60))
def _scheduled_rates_refresh_tick() -> None:
    from ui.scheduled_rates_refresh import try_refresh_applicable_rates

    result = try_refresh_applicable_rates()
    if result:
        stcg_pct = result["stcg_tax_rate"] * 100
        stt_pct = result["stt_rate"] * 100
        st.session_state["last_job_notice"] = (
            f"Applicable rates refreshed (STCG {stcg_pct:g}%, STT {stt_pct:g}%)."
        )


def main():
    main_start = time.perf_counter()
    db_ready = _init_app()
    sync_jobs_to_session()
    _scheduled_rates_refresh_tick()
    _scheduled_market_sync_tick(db_ready)

    with st.sidebar:
        page = st.radio(
            "Navigate",
            [
                "Trading",
                "Paper trading trend",
                "Pattern backtest",
                "Recommendations",
                "Mid day recommendation analysis",
                "Analysis & EOD",
                "Pattern definitions",
            ],
            label_visibility="collapsed",
            key="nav_page",
        )
        if not db_ready:
            st.sidebar.warning("Database offline — start Postgres, then use Refresh market data.")
        if notice := st.session_state.pop("last_job_notice", None):
            st.sidebar.success(notice)
        st.divider()
        from app.services.market_calendar import is_live_quote_session

        if page == "Trading" and db_ready and is_live_quote_session():
            st.caption(
                "Market is open — **Refresh market data** updates OHLCV only. "
                "Bracket exits (target / stop / 3:25 PM) rely on **Live polling** on the Positions tab."
            )
        if page == "Trading" and db_ready and st.button(
            "Refresh market data",
            use_container_width=True,
            disabled=is_any_job_running(),
        ):
            st.session_state["_market_sync_requested"] = True
        st.caption(
            "Auto-sync: **3:45 PM** and **6:00 PM IST** (trading days) — "
            "skipped if today's data was already synced after 4 PM."
        )
        sidebar_job_slot = st.empty()
        sidebar_job_slot.empty()
        run_background_job_watcher(slot=sidebar_job_slot)

    titles = {
        "Trading": "NIFTY Paper Trading",
        "Paper trading trend": "Paper Trading Trend",
        "Pattern backtest": "Pattern Backtesting",
        "Recommendations": "Recommendation Engine",
        "Mid day recommendation analysis": "Mid Day Recommendation Analysis",
        "Analysis & EOD": "Analysis & EOD Report",
        "Pattern definitions": "Pattern Definitions",
    }
    st.title(titles.get(page, "NIFTY Paper Trading"))
    # First-paint marker for UI smoke tests / blank-tab diagnosis.
    st.caption("App shell ready")
    st.markdown(
        '<div id="trading-app-shell" data-testid="trading-app-shell-ready"></div>',
        unsafe_allow_html=True,
    )

    from ui.tab_switch_audit import audit_page_render

    prev_page = st.session_state.get("_audit_last_nav_page")

    with audit_page_render(page, from_page=prev_page, db_ready=db_ready, main_start=main_start):
        if page == "Trading":
            if not db_ready:
                st.error(
                    "PostgreSQL is not running. Start Postgres, then use **Refresh market data** on the Trading tab."
                )
            else:
                render_trading_page()
        elif page == "Pattern backtest":
            render_backtest_page()
        elif page == "Pattern definitions":
            render_pattern_definitions_page()
        elif page == "Analysis & EOD":
            if not db_ready:
                st.error("PostgreSQL is not running — start Postgres to load trade analysis.")
            else:
                render_eod_analysis_page()
        elif page == "Paper trading trend":
            if not db_ready:
                st.error("PostgreSQL is not running — start Postgres to load trading trends.")
            else:
                render_paper_trading_trend_page()
        elif page == "Mid day recommendation analysis":
            if not db_ready:
                st.error("PostgreSQL is not running — start Postgres to run mid-day analysis.")
            else:
                render_midday_recommendations_page()
        else:
            render_recommendations_page()

    st.session_state["_audit_last_nav_page"] = page


if __name__ == "__main__":
    main()

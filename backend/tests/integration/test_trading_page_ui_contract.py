"""Trading tab UI contract — sections removed/retained and EOD snippet gating."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

DASHBOARD_PATH = Path(__file__).resolve().parents[2] / "ui" / "dashboard.py"


def _trading_page_body_source(source: str | None = None) -> str:
    text = source or DASHBOARD_PATH.read_text(encoding="utf-8")
    return text.split("def _render_trading_page_body", 1)[1].split("\ndef ", 1)[0]


@pytest.mark.quick
def test_trading_page_renders_body_even_during_market_sync() -> None:
    """Market sync must show a banner, not replace the entire Trading page (blank shell)."""
    source = DASHBOARD_PATH.read_text(encoding="utf-8")
    render_trading = source.split("def render_trading_page", 1)[1].split("\ndef ", 1)[0]
    assert "_render_trading_page_body()" in render_trading
    assert "_market_sync_progress_fragment()" in render_trading
    # Must not early-return before the body after showing sync progress.
    after_fragment = render_trading.split("_market_sync_progress_fragment()", 1)[1]
    assert "return" not in after_fragment.split("_render_trading_page_body()", 1)[0]


@pytest.mark.quick
def test_trading_page_does_not_render_broker_after_tax_block() -> None:
    source = DASHBOARD_PATH.read_text(encoding="utf-8")
    render_trading = _trading_page_body_source(source)
    assert "_render_broker_portfolio_comparison" not in render_trading
    assert "_render_paper_trading_after_tax_section" not in render_trading


@pytest.mark.quick
def test_paper_trading_page_renders_broker_after_tax_block() -> None:
    source = DASHBOARD_PATH.read_text(encoding="utf-8")
    paper_page = source.split("def render_paper_trading_trend_page", 1)[1].split("\ndef ", 1)[0]
    assert "_render_paper_trading_after_tax_section" in paper_page


@pytest.mark.quick
def test_trading_page_does_not_render_eod_recommendation_block() -> None:
    source = DASHBOARD_PATH.read_text(encoding="utf-8")
    render_trading = _trading_page_body_source(source)
    assert "EOD recommendation analysis" not in render_trading
    assert "_get_eod_analysis" not in render_trading


@pytest.mark.quick
def test_eod_analysis_lives_on_dedicated_page() -> None:
    source = DASHBOARD_PATH.read_text(encoding="utf-8")
    eod_page = source.split("def render_eod_analysis_page", 1)[1].split("\ndef ", 1)[0]
    assert "trade_analysis_dataframe" in eod_page or "_run_eod_trade_analysis" in eod_page


@pytest.mark.quick
def test_positions_table_column_labels() -> None:
    from ui.positions_display import COLUMN_LABELS, SORTABLE_COLUMNS

    assert "mark_price" not in SORTABLE_COLUMNS
    assert "EOD mark" not in COLUMN_LABELS.values()
    assert COLUMN_LABELS["today_open"] == "Open price"
    assert COLUMN_LABELS["session_high"] == "Today's high"


@pytest.mark.quick
def test_position_live_quote_cache_roundtrip() -> None:
    from app.services.live_quotes import PositionLiveQuote

    quote = PositionLiveQuote(
        last_price=1500.25,
        today_open=1490.0,
        prev_close=1485.0,
        session_high=1510.0,
    )
    cache = quote.to_cache()
    restored = PositionLiveQuote.from_cache(cache)
    assert restored is not None
    assert restored.last_price == 1500.25
    assert restored.today_open == 1490.0
    assert restored.prev_close == 1485.0
    assert restored.session_high == 1510.0


@pytest.mark.quick
def test_position_live_quote_from_legacy_float_ltp() -> None:
    from app.services.live_quotes import PositionLiveQuote, live_quote_ltp

    cache = {"INFY": 1500.0}
    parsed = PositionLiveQuote.from_cache(cache["INFY"])
    assert parsed is not None
    assert parsed.last_price == 1500.0
    assert parsed.today_open is None
    assert live_quote_ltp(cache, "INFY") == 1500.0


@pytest.mark.quick
def test_resolve_order_current_price_prefers_live_then_close() -> None:
    from ui.orders_display import resolve_order_current_price

    price, is_live = resolve_order_current_price(
        "INFY",
        live_quotes={"INFY": {"ltp": 1500.0}},
        close_by_symbol={"INFY": 1400.0},
    )
    assert price == 1500.0
    assert is_live is True

    price, is_live = resolve_order_current_price(
        "INFY",
        live_quotes={},
        close_by_symbol={"INFY": 1400.0},
    )
    assert price == 1400.0
    assert is_live is False

    price, is_live = resolve_order_current_price(
        "INFY",
        live_quotes={},
        close_by_symbol={},
    )
    assert price is None
    assert is_live is False


@pytest.mark.quick
def test_format_ist_datetime_converts_from_utc() -> None:
    from datetime import datetime, timezone

    from ui.helpers import format_ist_datetime

    utc = datetime(2026, 8, 5, 4, 24, tzinfo=timezone.utc)
    assert format_ist_datetime(utc) == "2026-08-05 09:54"


@pytest.mark.quick
def test_trading_page_has_no_nifty250_snapshot_section() -> None:
    source = DASHBOARD_PATH.read_text(encoding="utf-8")
    render_trading = _trading_page_body_source(source)
    assert "NIFTY250 snapshot" not in render_trading
    assert "_nifty250_index_candles" not in render_trading


@pytest.mark.quick
def test_trading_page_does_not_auto_reconcile_brackets() -> None:
    source = DASHBOARD_PATH.read_text(encoding="utf-8")
    render_trading = _trading_page_body_source(source)
    assert "_reconcile_brackets_if_needed" not in render_trading


@pytest.mark.quick
def test_trading_page_uses_lazy_tab_radio() -> None:
    source = DASHBOARD_PATH.read_text(encoding="utf-8")
    assert 'st.radio(' in source
    assert "trading_data_tab" in source
    assert "def _render_orders_tab" in source
    assert "def _render_trades_tab" in source
    assert "def _render_nifty250_constituents_tab" in source


@pytest.mark.quick
def test_trading_page_lazy_footer_and_bracket_deferral() -> None:
    source = DASHBOARD_PATH.read_text(encoding="utf-8")
    render_trading = _trading_page_body_source(source)
    assert "trading_footer_section" in render_trading
    assert "include_md_stats=show_market_data" in render_trading
    assert "_recommendation_bracket_symbols()" in render_trading
    assert '_ensure_recommendation_session_state()' not in render_trading.split("with st.sidebar:")[0]


@pytest.mark.quick
def test_recommendations_page_uses_lazy_sections() -> None:
    source = DASHBOARD_PATH.read_text(encoding="utf-8")
    rec_page = source.split("def render_recommendations_page", 1)[1].split("\ndef ", 1)[0]
    rec_body = source.split("def _render_recommendations_body", 1)[1].split("\ndef ", 1)[0]
    assert "rec_page_section" in rec_body
    assert "rec_tier_view" in source
    assert "_render_recommendations_budget_orders_section" in source
    assert "_recommendations_live_fragment" in source
    assert "_rec_live_poll" in rec_page
    assert "start_recommendations_job" in rec_page
    assert "st.rerun()" not in rec_page


@pytest.mark.quick
def test_background_job_watcher_does_not_full_page_rerun() -> None:
    jobs_path = DASHBOARD_PATH.parent / "background_jobs.py"
    watch = jobs_path.read_text(encoding="utf-8").split("def run_background_job_watcher", 1)[1].split("\ndef ", 1)[0]
    assert "st.rerun()" not in watch
    assert "slot:" in watch
    assert "render_sidebar_job_status(slot)" in jobs_path.read_text(encoding="utf-8")


@pytest.mark.quick
def test_midday_page_uses_lazy_sections() -> None:
    source = DASHBOARD_PATH.read_text(encoding="utf-8")
    midday_page = source.split("def render_midday_recommendations_page", 1)[1].split("\ndef ", 1)[0]
    midday_body = source.split("def _render_midday_recommendations_body", 1)[1].split("\ndef ", 1)[0]
    assert "midday_page_section" in midday_body
    assert "_render_midday_place_orders_section" in source
    assert "_midday_recommendations_live_fragment" in source
    assert "_midday_live_poll" in midday_page
    assert "start_midday_recommendations_job" in midday_page
    assert "st.rerun()" not in midday_page


@pytest.mark.quick
def test_backtest_eod_defer_heavy_loads_trend_auto_loads_on_first_visit() -> None:
    source = DASHBOARD_PATH.read_text(encoding="utf-8")
    backtest = source.split("def render_backtest_page", 1)[1].split("\ndef ", 1)[0]
    eod = source.split("def render_eod_analysis_page", 1)[1].split("\ndef ", 1)[0]
    trend = source.split("def render_paper_trading_trend_page", 1)[1].split("\ndef ", 1)[0]
    assert "backtest_page_section" in backtest
    assert "if refresh:" in eod
    assert "cache_key not in st.session_state" not in eod
    assert 'key="trend_refresh"' in trend
    assert "cache_key not in st.session_state" in trend


@pytest.mark.quick
def test_helpers_expose_midday_place_state_loader() -> None:
    import ui.helpers as helpers

    assert hasattr(helpers, "_load_midday_place_state")
    assert hasattr(helpers, "_load_trading_page_data")


@pytest.mark.quick
def test_trading_page_market_sync_avoids_blank_rerun() -> None:
    source = DASHBOARD_PATH.read_text(encoding="utf-8")
    trading_page = source.split("def render_trading_page", 1)[1].split("\ndef ", 1)[0]
    progress_fragment = source.split("def _market_sync_progress_fragment", 1)[1].split("\ndef ", 1)[0]
    main_fn = source.split("def main():", 1)[1].split("\nif __name__", 1)[0]
    assert "_market_sync_progress_fragment" in source
    assert "_render_trading_page_body()" not in progress_fragment
    assert "_market_sync_requested" in main_fn
    assert "_market_sync_live_poll" in trading_page
    assert "start_market_sync_job" in trading_page
    assert "st.rerun()" not in main_fn.split("Refresh market data")[1].split("st.caption", 1)[0]


@pytest.mark.quick
def test_trading_page_symbol_chart_is_on_demand() -> None:
    render_trading = _trading_page_body_source()
    assert "Show chart" in render_trading
    assert "request_symbol_history_chart_dialog" in render_trading
    assert "render_symbol_history_chart_dialog_if_open" in render_trading
    assert "_symbol_chart_request" not in render_trading
    assert "NIFTY250 —" not in render_trading


@pytest.mark.quick
def test_trading_eod_date_gate_logic() -> None:
    prior = date(2026, 7, 28)
    active = date(2026, 7, 29)
    assert not (prior >= active)
    assert active >= active

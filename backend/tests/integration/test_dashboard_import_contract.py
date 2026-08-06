"""Dashboard import contract — catches missing UI exports before deploy."""

from __future__ import annotations

import importlib
import sys
import types

import pytest

DASHBOARD_UI_IMPORTS: dict[str, tuple[str, ...]] = {
    "ui.helpers": (
        "_account",
        "_backtest_pattern_detail",
        "_candles",
        "_cancel_order",
        "_list_instruments",
        "_market_summary",
        "_orders",
        "_place_allocation_buy",
        "_place_order",
        "_positions",
        "_trades",
        "ensure_ready",
        "format_inr",
        "format_pct",
        "list_registered_patterns",
        "run_async",
    ),
    "ui.job_api": (
        "JobKind",
        "cancel_running_job",
        "is_any_job_running",
        "is_kind_running",
        "list_jobs",
        "poll_running_jobs",
        "render_sidebar_job_status",
        "run_background_job_watcher",
        "start_market_sync_job",
        "start_recommendations_job",
        "start_sim_backtest_job",
        "start_today_prediction_job",
        "sync_jobs_to_session",
    ),
    "ui.backtest_display": (
        "build_bearish_mismatch_summary",
        "build_bullish_summary_matrix",
        "build_validation_scorecard",
        "day_details_dataframe",
        "prediction_context",
        "style_bullish_summary",
    ),
    "ui.recommendations_display": (
        "allocation_dataframe",
        "allocation_simulation_dataframe",
        "allocation_summary_rows",
        "budget_simulation_comparison_dataframe",
        "patterns_dataframe",
        "recommendation_investment_dataframe",
        "recommendations_dataframe",
    ),
}

DASHBOARD_APP_IMPORTS: dict[str, tuple[str, ...]] = {
    "app.services.market_data_stats": (
        "get_market_data_stats",
        "fetch_market_data_stats",
        "MarketDataStats",
    ),
    "app.services.simulation_cache": ("load_cached_simulation",),
    "app.schemas": ("OrderSide", "OrderType", "PlaceOrderRequest"),
}


@pytest.mark.quick
@pytest.mark.parametrize(
    ("module_name", "symbols"),
    [
        *((name, symbols) for name, symbols in DASHBOARD_UI_IMPORTS.items()),
        *((name, symbols) for name, symbols in DASHBOARD_APP_IMPORTS.items()),
    ],
)
def test_dashboard_import_symbols_exist(module_name: str, symbols: tuple[str, ...]) -> None:
    module = importlib.import_module(module_name)
    missing = [symbol for symbol in symbols if not hasattr(module, symbol)]
    assert not missing, f"{module_name} missing exports: {missing}"


@pytest.mark.quick
def test_job_api_reexports_background_jobs() -> None:
    import ui.background_jobs as bg
    import ui.job_api as job_api

    for name in DASHBOARD_UI_IMPORTS["ui.job_api"]:
        assert hasattr(job_api, name), f"ui.job_api missing {name}"
        assert getattr(job_api, name) is getattr(bg, name), f"ui.job_api.{name} stale re-export"


@pytest.mark.quick
def test_job_completion_survives_module_reload() -> None:
    """Worker updates must remain visible after ensure_fresh_ui_modules()."""
    import asyncio
    import time

    import ui.background_jobs as bg
    from ui import job_registry
    from ui.async_runner import reset_for_tests
    from ui.streamlit_imports import ensure_fresh_ui_modules

    reset_for_tests()
    with job_registry._lock:
        job_registry._jobs_by_session.clear()

    async def finish(progress_callback):
        progress_callback(50, 100, "Halfway", None)
        await asyncio.sleep(0.25)
        return {"ok": True}, {"lines": []}

    job_id = bg.start_async_job(bg.JobKind.RECOMMENDATIONS, "reload completion", finish)
    assert job_id

    ensure_fresh_ui_modules()

    session_key = bg._session_key()
    deadline = time.time() + 10
    job = None
    while time.time() < deadline:
        job = job_registry.jobs_for_session(session_key).get(job_id)
        if job and job.get("status") == "completed":
            break
        time.sleep(0.05)

    assert job is not None and job.get("status") == "completed"
    assert job.get("progress") == 1.0


@pytest.mark.quick
def test_streamlit_reload_preserves_background_jobs() -> None:
    """Reloading UI modules must not wipe the in-memory job registry."""
    import asyncio
    import time

    import ui.background_jobs as bg
    from ui.streamlit_imports import ensure_fresh_ui_modules

    async def noop(progress_callback):
        progress_callback("working")
        await asyncio.sleep(0.2)
        return True

    job_id = bg.start_async_job(bg.JobKind.TODAY_PREDICTION, "preserve test", noop)
    assert job_id

    ensure_fresh_ui_modules()

    with bg._lock:
        still_there = job_id in bg._jobs_for_session(bg._session_key())
    assert still_there, "ensure_fresh_ui_modules cleared background job state"

    bg.cancel_running_job(bg.JobKind.TODAY_PREDICTION)


@pytest.mark.quick
def test_start_recommendations_job_accepts_max_target_after_refresh() -> None:
    import inspect

    from ui.streamlit_imports import ensure_job_api_fresh

    ensure_job_api_fresh()
    job_api = importlib.import_module("ui.job_api")
    params = inspect.signature(job_api.start_recommendations_job).parameters
    assert "max_target_profit_pct" in params


@pytest.mark.quick
def test_dashboard_survives_stale_job_api_cache() -> None:
    """Simulates Streamlit keeping an old ui.job_api in sys.modules."""
    import ui.background_jobs as bg

    importlib.import_module("ui.job_api")
    stale = types.ModuleType("ui.job_api")
    stale.JobKind = bg.JobKind
    sys.modules["ui.job_api"] = stale

    from ui.streamlit_imports import ensure_fresh_ui_modules

    ensure_fresh_ui_modules()

    job_api = importlib.import_module("ui.job_api")
    assert callable(getattr(job_api, "is_any_job_running", None))

    sys.modules.pop("ui.dashboard", None)
    dashboard = importlib.import_module("ui.dashboard")
    assert dashboard is not None


@pytest.mark.quick
def test_models_mappers_survive_streamlit_purge() -> None:
    """Purge/reimport must not leave Instrument unable to resolve PaperOrder."""
    from sqlalchemy.orm import class_mapper

    from ui.streamlit_imports import ensure_models_fresh

    ensure_models_fresh()

    import app.models as models

    instrument_mapper = class_mapper(models.Instrument)
    assert "PaperOrder" in instrument_mapper.relationships["orders"].argument

    ensure_models_fresh()

    import app.models as models_after

    class_mapper(models_after.Instrument)
    class_mapper(models_after.PaperOrder)


@pytest.mark.quick
def test_backtest_orm_queries_survive_model_purge() -> None:
    """BacktestEngine must not keep stale Instrument classes after app.models reload."""
    from sqlalchemy import select

    from ui.streamlit_imports import ensure_models_fresh

    ensure_models_fresh()

    from app.services.backtest import BacktestEngine, _orm_models

    Instrument, *_ = _orm_models()
    stmt = select(Instrument).where(Instrument.symbol == "NIFTY50")
    assert "instruments" in str(stmt)

    ensure_models_fresh()

    Instrument_after, *_ = _orm_models()
    stmt_after = select(Instrument_after).where(Instrument_after.symbol == "NIFTY50")
    assert "instruments" in str(stmt_after)

    BacktestEngine(universe="NIFTY250")


@pytest.mark.quick
def test_dashboard_entrypoint_imports() -> None:
    sys.modules.pop("ui.dashboard", None)
    module = importlib.import_module("ui.dashboard")
    assert module is not None


@pytest.mark.quick
def test_hot_reload_import_error_detection() -> None:
    from ui.streamlit_imports import is_hot_reload_import_error

    assert is_hot_reload_import_error(
        AttributeError("'NoneType' object has no attribute '__dict__'")
    )
    assert is_hot_reload_import_error(KeyError("app.services.trade_plans"))
    assert not is_hot_reload_import_error(ValueError("bad bracket"))

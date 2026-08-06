"""UI module contracts — symbols and imports used by the Streamlit portal."""

from __future__ import annotations

import inspect

import pytest

from app.services.simulation_cache import load_cached_simulation


@pytest.mark.quick
def test_dashboard_page_renderers_exist() -> None:
    import ui.dashboard as dashboard

    for name in (
        "render_trading_page",
        "render_backtest_page",
        "render_recommendations_page",
        "render_eod_analysis_page",
        "render_pattern_definitions_page",
        "main",
    ):
        assert hasattr(dashboard, name), f"ui.dashboard missing {name}"
        assert callable(getattr(dashboard, name))


@pytest.mark.quick
def test_dashboard_imports_recommendation_chart() -> None:
    import ui.dashboard as dashboard

    assert hasattr(dashboard, "build_recommendation_chart")
    import ui.dashboard as dashboard

    assert hasattr(dashboard, "main")
    # main() runs only under streamlit (__name__ == "__main__"), not on import.
    assert dashboard.__name__ == "ui.dashboard"


@pytest.mark.quick
def test_helpers_exports_used_by_dashboard() -> None:
    import ui.helpers as helpers

    required = (
        "_run_backtest",
        "_run_today_prediction",
        "_load_cached_simulation",
        "_backtest_pattern_detail",
        "run_async",
        "ensure_ready",
        "list_registered_patterns",
    )
    for name in required:
        assert hasattr(helpers, name), f"ui.helpers missing {name}"


@pytest.mark.quick
def test_backtest_page_imports_resolve() -> None:
    """Symbols referenced inside render_backtest_page must be importable."""
    from app.config import settings
    from app.defaults import DEFAULT_SIMULATION_UNIVERSE
    from app.services.nifty_universe import DEFAULT_UNIVERSE, list_universe_options

    assert callable(list_universe_options)
    assert DEFAULT_UNIVERSE in list_universe_options()
    assert getattr(settings, "default_simulation_universe", DEFAULT_SIMULATION_UNIVERSE)


@pytest.mark.quick
def test_recommendations_page_imports_resolve() -> None:
    from app.config import settings
    from app.defaults import DEFAULT_DAILY_TRADING_BUDGET_INR

    budget = float(getattr(settings, "daily_trading_budget_inr", DEFAULT_DAILY_TRADING_BUDGET_INR))
    assert budget > 0


@pytest.mark.quick
def test_job_api_exports_cancel() -> None:
    import ui.job_api as job_api

    assert hasattr(job_api, "cancel_running_job")
    assert callable(job_api.cancel_running_job)


@pytest.mark.quick
def test_simulation_cache_loader_is_async() -> None:
    assert inspect.iscoroutinefunction(load_cached_simulation)

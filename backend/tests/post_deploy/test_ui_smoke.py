"""Post-deployment UI smoke tests — modules and page entrypoints must load cleanly."""

from __future__ import annotations

import importlib
import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.post_deploy.api_catalog import APP_MODULES, UI_MODULES, UI_PAGE_RENDERERS


@pytest.mark.post_deploy
@pytest.mark.parametrize("module_name", UI_MODULES)
def test_ui_module_imports(module_name: str) -> None:
    module = importlib.import_module(module_name)
    assert module is not None


@pytest.mark.post_deploy
@pytest.mark.parametrize("module_name", APP_MODULES)
def test_app_module_imports(module_name: str) -> None:
    module = importlib.import_module(module_name)
    assert module is not None


@pytest.mark.post_deploy
def test_dashboard_page_renderers_exist() -> None:
    import ui.dashboard as dashboard

    for name in UI_PAGE_RENDERERS:
        assert hasattr(dashboard, name), f"ui.dashboard missing {name}"
        assert callable(getattr(dashboard, name))


@pytest.mark.post_deploy
def test_streamlit_fresh_reload_chain() -> None:
    from ui.streamlit_imports import ensure_fresh_ui_modules

    ensure_fresh_ui_modules()
    import ui.helpers as helpers

    assert hasattr(helpers, "_load_trading_page_data")


@pytest.mark.post_deploy
@pytest.mark.asyncio
async def test_trading_page_data_load_not_server_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bundled trading DB load path must not raise (mocked — no live Streamlit)."""
    from app.schemas import AccountOut
    from app.services.market_data_stats import MarketDataStats
    from ui import helpers

    fake_session = MagicMock(name="session")

    class _FakeUiSession:
        async def __aenter__(self):
            return fake_session

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(helpers, "ui_session", lambda: _FakeUiSession())
    monkeypatch.setattr(helpers, "_list_chart_instruments_for", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        helpers,
        "PaperTradingService",
        lambda session: MagicMock(
            get_account_summary=AsyncMock(
                return_value=AccountOut(
                    name="Paper",
                    cash_balance=0,
                    equity_value=0,
                    total_value=0,
                    unrealized_pnl=0,
                    realized_pnl=0,
                    initial_cash=0,
                )
            ),
            list_positions=AsyncMock(return_value=[]),
        ),
    )
    monkeypatch.setattr(
        "app.services.budget_portfolio.normalize_legacy_paper_account",
        AsyncMock(),
    )
    monkeypatch.setattr(helpers, "_market_summary_for", AsyncMock(return_value=[]))
    monkeypatch.setattr("ui.streamlit_imports.ensure_market_data_stats_fresh", lambda: None)
    monkeypatch.setattr(
        "app.services.market_data_stats.get_market_data_stats",
        AsyncMock(
            return_value=MarketDataStats(
                stocks_with_data=0,
                earliest_date=None,
                latest_date=None,
                simulation_universe=None,
                simulation_date=None,
                simulation_saved_at=None,
                simulation_from_cache=False,
                top_patterns=[],
            )
        ),
    )

    result = await helpers._load_trading_page_data()
    assert len(result) == 5


@pytest.mark.post_deploy
def test_job_api_exports_complete() -> None:
    from ui import job_api

    required = (
        "JobKind",
        "cancel_running_job",
        "is_any_job_running",
        "start_market_sync_job",
        "start_sim_backtest_job",
        "start_recommendations_job",
        "start_today_prediction_job",
    )
    for name in required:
        assert hasattr(job_api, name), f"ui.job_api missing {name}"


@pytest.mark.post_deploy
def test_market_data_stats_public_api() -> None:
    from app.services.market_data_stats import fetch_market_data_stats, get_market_data_stats

    assert inspect.iscoroutinefunction(get_market_data_stats)
    assert inspect.iscoroutinefunction(fetch_market_data_stats)
    assert "session" in inspect.signature(get_market_data_stats).parameters

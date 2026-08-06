"""Ensure all application and UI modules import without error."""

from __future__ import annotations

import importlib
import sys

import pytest

# Modules that must import cleanly for the portal and API to work.
REQUIRED_MODULES = [
    "app.config",
    "app.defaults",
    "app.main",
    "app.models",
    "app.models.audit_log",
    "app.models.base",
    "app.db.session",
    "app.db.ui_session",
    "app.providers",
    "app.providers.nse_provider",
    "app.services.backtest",
    "app.services.backtest_loader",
    "app.services.simulation_cache",
    "app.services.audit_types",
    "app.services.audit",
    "app.middleware.audit",
    "app.services.nifty_universe",
    "app.services.recommendation_engine",
    "app.services.budget_allocator",
    "app.services.trade_tax",
    "app.services.ingestion",
    "app.services.market_data_stats",
    "app.services.paper_trading",
    "app.strategies.registry",
    "app.strategies.patterns",
    "app.api.routes.backtest",
    "app.api.routes.market",
    "ui.helpers",
    "ui.backtest_display",
    "ui.recommendation_helpers",
    "ui.recommendations_display",
    "ui.dashboard",
    "ui.background_jobs",
    "ui.job_api",
    "ui.streamlit_imports",
    "ui.async_runner",
]


MODEL_MODULES = frozenset({"app.models", "app.models.audit_log", "app.models.base"})


@pytest.mark.quick
@pytest.mark.parametrize("module_name", REQUIRED_MODULES)
def test_module_imports(module_name: str) -> None:
    # SQLAlchemy declarative models cannot be re-registered on the same Base/metadata.
    if module_name in MODEL_MODULES or module_name.startswith("app.models."):
        module = importlib.import_module(module_name)
    else:
        sys.modules.pop(module_name, None)
        module = importlib.import_module(module_name)
    assert module is not None


@pytest.mark.quick
def test_backtest_loader_accepts_universe() -> None:
    from app.services.backtest_loader import BacktestEngine

    engine = BacktestEngine(universe="NIFTY250")
    assert engine.universe == "NIFTY250"

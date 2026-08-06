"""Config and defaults must expose fields the UI depends on."""

from __future__ import annotations

import pytest

from app.config import settings
from app.defaults import DEFAULT_DAILY_TRADING_BUDGET_INR, DEFAULT_SIMULATION_UNIVERSE, DEFAULT_MAX_TARGET_PROFIT_PCT, DEFAULT_PAPER_TRADING_RETENTION_DAYS


@pytest.mark.quick
def test_defaults_constants() -> None:
    assert DEFAULT_SIMULATION_UNIVERSE == "NIFTY250"
    assert DEFAULT_DAILY_TRADING_BUDGET_INR == 50_000.0


@pytest.mark.quick
def test_settings_required_attributes() -> None:
    required = (
        "database_url",
        "data_provider",
        "default_simulation_universe",
        "daily_trading_budget_inr",
        "max_target_profit_pct",
        "paper_trading_retention_days",
        "conservative_exit_ratio",
        "stcg_tax_rate",
    )
    for name in required:
        assert hasattr(settings, name), f"Settings missing attribute: {name}"
        assert getattr(settings, name) is not None


@pytest.mark.quick
def test_settings_universe_and_budget_defaults() -> None:
    assert settings.default_simulation_universe == DEFAULT_SIMULATION_UNIVERSE
    assert settings.daily_trading_budget_inr == DEFAULT_DAILY_TRADING_BUDGET_INR
    assert settings.max_target_profit_pct == DEFAULT_MAX_TARGET_PROFIT_PCT
    assert settings.paper_trading_retention_days == DEFAULT_PAPER_TRADING_RETENTION_DAYS

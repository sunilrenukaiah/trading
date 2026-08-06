"""Regression tests derived from audit_logs failures and portal errors (last 4 days).

Covers: stale Streamlit module cache, BacktestReport field contract, dual-broker tax,
settings getattr fallbacks, asyncpg connect args, ORM mapper survival, and audit isolation.
"""

from __future__ import annotations

import importlib
import sys
import types
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.config import settings
from app.defaults import (
    DEFAULT_DAILY_TRADING_BUDGET_INR,
    DEFAULT_GST_RATE,
    DEFAULT_SIMULATION_UNIVERSE,
    DEFAULT_ZERODHA_DP_CHARGE_PER_SCRIP_INR,
)
from app.services.backtest import BacktestReport, PatternResult
from app.services.simulation_cache import deserialize_report, serialize_report

def _minimal_pattern() -> PatternResult:
    return PatternResult(
        pattern_id="test_pattern",
        pattern_name="Test Pattern",
        total_correct=1,
        total_signals=2,
        daily_scores=[0.5],
        stock_correct={"RELIANCE": 1},
        stock_signals={"RELIANCE": 1},
        day_details=[],
    )


def _minimal_report() -> BacktestReport:
    return BacktestReport(
        eval_days=5,
        lookback_days=10,
        stock_count=1,
        patterns=[_minimal_pattern()],
        universe="NIFTY250",
        symbols=["RELIANCE"],
    )


@pytest.mark.quick
def test_backtest_report_exposes_patterns_not_pattern_results() -> None:
    """Audit: AttributeError — BacktestReport has no attribute pattern_results."""
    report = _minimal_report()
    assert report.patterns
    assert "patterns" in BacktestReport.__dataclass_fields__
    assert "pattern_results" not in BacktestReport.__dataclass_fields__


@pytest.mark.quick
def test_simulation_cache_roundtrip_uses_patterns_field() -> None:
    report = _minimal_report()
    payload = serialize_report(report)
    assert "patterns" in payload
    assert "pattern_results" not in payload

    restored = deserialize_report(payload)
    assert len(restored.patterns) == 1
    assert restored.patterns[0].pattern_id == "test_pattern"


@pytest.mark.quick
def test_stale_trade_tax_module_refreshed_by_streamlit_imports() -> None:
    from ui.streamlit_imports import ensure_trade_tax_fresh

    stale = types.ModuleType("app.services.trade_tax")
    stale.summarize_sell_trades_after_tax = lambda sells: None  # type: ignore[attr-defined]
    sys.modules["app.services.trade_tax"] = stale

    ensure_trade_tax_fresh()

    module = importlib.import_module("app.services.trade_tax")
    assert hasattr(module, "DualBrokerRealizedPnlSummary")
    assert hasattr(module, "summarize_sell_trades_dual_broker")


@pytest.mark.quick
def test_stale_defaults_module_refreshed_by_streamlit_imports() -> None:
    from ui.streamlit_imports import ensure_defaults_fresh

    stale = types.ModuleType("app.defaults")
    sys.modules["app.defaults"] = stale

    ensure_defaults_fresh()

    module = importlib.import_module("app.defaults")
    assert hasattr(module, "DEFAULT_GST_RATE")
    assert module.DEFAULT_GST_RATE == DEFAULT_GST_RATE


@pytest.mark.quick
def test_stale_budget_portfolio_module_refreshed() -> None:
    from ui.streamlit_imports import ensure_budget_portfolio_fresh

    stale = types.ModuleType("app.services.budget_portfolio")
    stale.compute_budget_view = lambda *a, **k: None  # type: ignore[attr-defined]
    sys.modules["app.services.budget_portfolio"] = stale

    ensure_budget_portfolio_fresh()

    module = importlib.import_module("app.services.budget_portfolio")
    assert hasattr(module, "portfolio_total_at_cost")
    assert hasattr(module, "portfolio_total_with_unrealized")


@pytest.mark.quick
def test_ensure_fresh_ui_modules_restores_trade_tax_and_defaults() -> None:
    from ui.streamlit_imports import ensure_fresh_ui_modules

    for name in (
        "app.defaults",
        "app.services.applicable_rates",
        "app.services.trade_tax",
        "app.services.budget_portfolio",
    ):
        sys.modules.pop(name, None)

    ensure_fresh_ui_modules()

    defaults = importlib.import_module("app.defaults")
    applicable_rates = importlib.import_module("app.services.applicable_rates")
    trade_tax = importlib.import_module("app.services.trade_tax")
    budget = importlib.import_module("app.services.budget_portfolio")

    assert hasattr(defaults, "DEFAULT_GST_RATE")
    assert "brokerage_min_per_share_inr" in applicable_rates.ApplicableRates.__dataclass_fields__
    assert hasattr(trade_tax, "DualBrokerRealizedPnlSummary")
    assert hasattr(budget, "portfolio_total_at_cost")


@pytest.mark.quick
def test_ensure_live_quotes_fresh_after_stale_module() -> None:
    """Background poll thread must not fail when trade_plans reloads before live_quotes."""
    from ui.streamlit_imports import ensure_live_quotes_fresh

    stale = types.ModuleType("app.services.live_quotes")

    async def _stale_fetch(*_args, **_kwargs):
        return {}

    stale.fetch_live_quotes = _stale_fetch
    sys.modules["app.services.live_quotes"] = stale
    sys.modules.pop("app.services.trade_plans", None)

    ensure_live_quotes_fresh()

    live_quotes = importlib.import_module("app.services.live_quotes")
    assert hasattr(live_quotes, "fetch_live_quotes")
    assert hasattr(live_quotes, "merge_session_extremes")
    from app.providers.base import QuoteData, SessionQuote

    assert "day_open" in QuoteData.__dataclass_fields__
    providers = importlib.import_module("app.providers.base")
    assert providers.QuoteData is QuoteData

    trade_plans = importlib.import_module("app.services.trade_plans")
    assert trade_plans.TradePlanService is not None
    _ = SessionQuote(last_price=Decimal("100"))


@pytest.mark.quick
def test_ensure_recommendation_cache_fresh_after_stale_engine() -> None:
    """Cache reload must not fail when recommendation_engine is stale on disk."""
    from ui.streamlit_imports import ensure_recommendation_cache_fresh

    stale = types.ModuleType("app.services.recommendation_engine")
    live = importlib.import_module("app.services.recommendation_engine")
    stale.StockRecommendation = live.StockRecommendation
    stale.PatternRanking = live.PatternRanking
    stale.RecommendationReport = live.RecommendationReport
    stale.all_report_recommendations = live.all_report_recommendations
    stale.apply_price_bucket_sanitize = live.apply_price_bucket_sanitize
    sys.modules["app.services.recommendation_engine"] = stale
    sys.modules.pop("app.services.recommendation_cache", None)

    ensure_recommendation_cache_fresh()

    engine = importlib.import_module("app.services.recommendation_engine")
    assert hasattr(engine, "coerce_stock_recommendation")
    assert hasattr(engine, "normalize_recommendation_report")


@pytest.mark.quick
def test_ensure_fresh_ui_modules_restores_budget_allocator_and_bracket_utils() -> None:
    """Allocator refresh must expose skipped_invalid/backfill after deploy."""
    from ui.streamlit_imports import _BUDGET_ALLOCATOR, _force_reimport

    sys.modules.pop(_BUDGET_ALLOCATOR, None)
    sys.modules.pop("app.services.recommendation_cache", None)

    allocator = importlib.import_module(_BUDGET_ALLOCATOR)
    assert "skipped_invalid" in allocator.BudgetAllocationReport.__dataclass_fields__
    assert "backfilled_symbols" in allocator.BudgetAllocationReport.__dataclass_fields__

    stale = types.ModuleType(_BUDGET_ALLOCATOR)
    stale.allocate_budget = lambda *a, **k: None
    sys.modules[_BUDGET_ALLOCATOR] = stale

    refreshed = _force_reimport(_BUDGET_ALLOCATOR)
    assert "skipped_invalid" in refreshed.BudgetAllocationReport.__dataclass_fields__
    assert "backfilled_symbols" in refreshed.BudgetAllocationReport.__dataclass_fields__

    bracket_utils = importlib.import_module("app.services.bracket_utils")
    assert bracket_utils.is_valid_bracket_levels(100.0, 110.0, 95.0) is True
    assert bracket_utils.is_valid_bracket_levels(100.0, 100.0, 95.0) is False


@pytest.mark.quick
def test_stale_applicable_rates_module_refreshed() -> None:
    from ui.streamlit_imports import ensure_applicable_rates_fresh

    stale = types.ModuleType("app.services.applicable_rates")

    class _StaleRates:
        stcg_tax_rate = 0.2
        stt_rate = 0.001
        stamp_duty_rate = 0.00015
        brokerage_rate = 0.003
        conservative_exit_ratio = 0.5

    stale.ApplicableRates = _StaleRates
    stale.get_applicable_rates = lambda: _StaleRates()
    stale.reset_applicable_rates_cache = lambda: None
    sys.modules["app.services.applicable_rates"] = stale

    ensure_applicable_rates_fresh()

    module = importlib.import_module("app.services.applicable_rates")
    rates = module.get_applicable_rates()
    assert hasattr(rates, "brokerage_min_per_share_inr")


@pytest.mark.quick
def test_compute_net_profit_tolerates_stale_rates_instance() -> None:
    """Portal: ApplicableRates missing brokerage_min_per_share_inr after hot reload."""
    from types import SimpleNamespace

    from app.services.trade_tax import compute_net_profit

    stale_rates = SimpleNamespace(
        stcg_tax_rate=0.2,
        stt_rate=0.001,
        stamp_duty_rate=0.00015,
        brokerage_rate=0.003,
        conservative_exit_ratio=0.5,
    )

    def _stale_get_rates():
        return stale_rates

    import app.services.trade_tax as trade_tax_mod

    original = trade_tax_mod.get_applicable_rates
    trade_tax_mod.get_applicable_rates = _stale_get_rates
    try:
        row = compute_net_profit(10, 100.0, 110.0)
    finally:
        trade_tax_mod.get_applicable_rates = original

    assert row.net_profit_after_tax > 0


@pytest.mark.quick
def test_dual_broker_summary_has_sharekhan_and_zerodha() -> None:
    from app.services.trade_tax import DualBrokerRealizedPnlSummary, summarize_sell_trades_dual_broker

    dual = summarize_sell_trades_dual_broker([(10, 100.0, 110.0)])
    assert isinstance(dual, DualBrokerRealizedPnlSummary)
    assert hasattr(dual, "sharekhan")
    assert hasattr(dual, "zerodha")
    assert dual.zerodha.total_dp_charges >= dual.sharekhan.total_dp_charges


@pytest.mark.quick
def test_realized_pnl_helper_returns_dual_broker_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Portal: 'RealizedPnlAfterTaxSummary' has no attribute sharekhan."""
    from contextlib import asynccontextmanager

    from app.services.trade_tax import RealizedPnlAfterTaxSummary
    from ui import helpers

    zero = RealizedPnlAfterTaxSummary(0.0, 0.0, 0.0, 0.0)

    class _FakeSession:
        async def scalars(self, _stmt):
            class _R:
                def all(self):
                    return []

            return _R()

    @asynccontextmanager
    async def _fake_ui_session():
        yield _FakeSession()

    class _FakeSvc:
        async def get_default_account(self):
            return SimpleNamespace(id=1)

    monkeypatch.setattr(helpers, "ui_session", _fake_ui_session)
    monkeypatch.setattr(helpers, "PaperTradingService", lambda _s: _FakeSvc())

    import asyncio

    result = asyncio.run(helpers._realized_pnl_after_tax_summary())
    # ensure_trade_tax_fresh() may reimport trade_tax — avoid isinstance across module reloads.
    assert hasattr(result, "sharekhan")
    assert hasattr(result, "zerodha")
    assert hasattr(result.sharekhan, "net_after_tax")
    assert hasattr(result.zerodha, "net_after_tax")
    assert result.zerodha.net_after_tax == zero.net_after_tax


@pytest.mark.quick
def test_settings_getattr_fallback_when_stale_cache() -> None:
    """Portal: Settings missing daily_trading_budget_inr / default_simulation_universe."""
    stale = SimpleNamespace(database_url="postgresql+asyncpg://x", data_provider="mock")

    budget = float(
        getattr(stale, "daily_trading_budget_inr", DEFAULT_DAILY_TRADING_BUDGET_INR)
    )
    universe = getattr(stale, "default_simulation_universe", DEFAULT_SIMULATION_UNIVERSE)

    assert budget == DEFAULT_DAILY_TRADING_BUDGET_INR
    assert universe == DEFAULT_SIMULATION_UNIVERSE


@pytest.mark.quick
def test_defaults_exports_required_by_trade_tax() -> None:
    from app import defaults

    required = (
        "DEFAULT_GST_RATE",
        "DEFAULT_ZERODHA_DP_CHARGE_PER_SCRIP_INR",
        "DEFAULT_BROKERAGE_MIN_PER_SHARE_INR",
        "DEFAULT_EXCHANGE_TXN_RATE",
        "DEFAULT_SEBI_TURNOVER_RATE",
    )
    missing = [name for name in required if not hasattr(defaults, name)]
    assert not missing, f"app.defaults missing: {missing}"
    assert defaults.DEFAULT_ZERODHA_DP_CHARGE_PER_SCRIP_INR == DEFAULT_ZERODHA_DP_CHARGE_PER_SCRIP_INR


@pytest.mark.quick
def test_database_url_has_no_asyncpg_options_query_param() -> None:
    """Audit: TypeError connect() got unexpected keyword argument 'options'."""
    from sqlalchemy.engine import make_url

    url = make_url(settings.database_url)
    assert "options" not in dict(url.query), (
        "DATABASE_URL must not use ?options= — asyncpg rejects it as a connect kwarg"
    )


@pytest.mark.quick
def test_ui_session_connect_args_exclude_options_kwarg() -> None:
    """UI engine must not pass ?options= style kwargs through to asyncpg.connect."""
    import inspect

    import app.db.ui_session as ui_session_mod

    source = inspect.getsource(ui_session_mod.ensure_ui_db)
    assert "connect_args" in source
    assert '"options"' not in source
    assert "'options'" not in source


@pytest.mark.quick
def test_backtest_orm_mappers_healthy_after_double_model_purge() -> None:
    """Audit: ArgumentError / InvalidRequestError — stale Instrument mapper after reload."""
    from sqlalchemy.orm import class_mapper

    from ui.streamlit_imports import ensure_models_fresh

    ensure_models_fresh()
    from app.services.backtest import BacktestEngine, _orm_models

    Instrument, *_ = _orm_models()
    class_mapper(Instrument)
    BacktestEngine(universe="NIFTY250")

    ensure_models_fresh()
    Instrument_after, Ohlcv_after, *_ = _orm_models()
    class_mapper(Instrument_after)
    class_mapper(Ohlcv_after)

    from sqlalchemy import select

    stmt = select(Instrument_after).where(Instrument_after.symbol == "RELIANCE")
    assert "instruments" in str(stmt)


@pytest.mark.quick
def test_background_job_failure_does_not_persist_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Audit noise: 115x ValueError job failed once from test_background_job_failure_not_retried."""
    import time

    import ui.background_jobs as bg
    from app.services.audit_backends.registry import get_audit_writer
    from app.services.audit_backends.noop import NoOpAuditWriter

    assert isinstance(get_audit_writer(), NoOpAuditWriter)

    calls = {"count": 0}

    async def failing_coro(progress_callback):
        calls["count"] += 1
        raise ValueError("job failed once")

    monkeypatch.setattr(bg, "_session_key", lambda: "audit-isolation-session")

    job_id = bg.start_async_job(bg.JobKind.TODAY_PREDICTION, "fail once", failing_coro)
    assert job_id

    deadline = time.time() + 5
    while time.time() < deadline:
        with bg._lock:
            job = bg._jobs_for_session("audit-isolation-session").get(job_id)
        if job and job["status"] == "failed":
            break
        time.sleep(0.05)

    assert calls["count"] == 1
    writer = get_audit_writer()
    assert isinstance(writer, NoOpAuditWriter)


@pytest.mark.quick
def test_dashboard_backtest_display_imports_not_helpers() -> None:
    """Portal ImportError: day_details_dataframe from ui.helpers."""
    import ui.backtest_display as display

    assert hasattr(display, "day_details_dataframe")
    assert callable(display.day_details_dataframe)


@pytest.mark.quick
def test_helpers_load_cached_simulation_export_exists() -> None:
    """Portal ImportError: _load_cached_simulation from ui.helpers."""
    import ui.helpers as helpers

    assert hasattr(helpers, "_load_cached_simulation")
    assert callable(helpers._load_cached_simulation)

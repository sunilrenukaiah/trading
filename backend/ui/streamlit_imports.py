"""Reload cached UI modules on each Streamlit script run."""

from __future__ import annotations

import importlib
import importlib.util
import sys
import threading
import time

_MARKET_DATA_STATS = "app.services.market_data_stats"
_APP_DEFAULTS = "app.defaults"
_BUDGET_ALLOCATOR = "app.services.budget_allocator"
_BUDGET_PORTFOLIO = "app.services.budget_portfolio"
_BROKER_DELIVERY_PROFILES = "app.services.broker_delivery_profiles"
_APPLICABLE_RATES = "app.services.applicable_rates"
_TRADE_TAX = "app.services.trade_tax"
_PAPER_TRADING = "app.services.paper_trading"
_INTRADAY_CHART = "app.services.intraday_chart"
_LIVE_QUOTES = "app.services.live_quotes"
_RECOMMENDATION_CACHE = "app.services.recommendation_cache"
_RECOMMENDATION_ENGINE = "app.services.recommendation_engine"
_NIFTY_UNIVERSE = "app.services.nifty_universe"
_APP_MODELS = "app.models"
_APP_MODELS_BASE = "app.models.base"
_APP_MODELS_AUDIT = "app.models.audit_log"

_reimport_lock = threading.RLock()

_TRADE_PLANS = "app.services.trade_plans"

_JOB_API = "ui.job_api"
_JOB_API_EXPORTS = (
    "JobKind",
    "cancel_running_job",
    "is_any_job_running",
    "is_kind_running",
    "list_jobs",
    "poll_running_jobs",
    "render_sidebar_job_status",
    "start_market_sync_job",
    "start_midday_recommendations_job",
    "start_recommendations_job",
    "start_sim_backtest_job",
    "start_today_prediction_job",
    "sync_jobs_to_session",
)

# Never reload — holds job registry and asyncio loop across reruns.
_STATEFUL_UI_MODULES = frozenset(
    {
        "ui.async_runner",
        "ui.background_jobs",
    }
)

# Safe to reload when code changes (no cross-run mutable state).
_UI_MODULE_CHAIN = (
    _MARKET_DATA_STATS,
    _INTRADAY_CHART,
    _LIVE_QUOTES,
    _BUDGET_ALLOCATOR,
    "app.services.trade_plans",
    "ui.helpers",
    "ui.recommendation_helpers",
    "ui.recommendation_chart",
    "ui.position_intraday_chart",
)

# Always drop and reimport — importlib.reload can leave partial stale exports.
_FORCE_REIMPORT = frozenset(
    {
        _MARKET_DATA_STATS,
        _INTRADAY_CHART,
        _RECOMMENDATION_CACHE,
        _LIVE_QUOTES,
        _BUDGET_ALLOCATOR,
        _NIFTY_UNIVERSE,
    }
)

_MODEL_DEPENDENTS = (
    "app.services.trade_plans",
    "app.services.recommendation_cache",
    "app.services.eod_trade_analysis",
    "app.services.backtest",
    "app.services.backtest_loader",
    "app.services.simulation_cache",
    "app.services.ingestion",
    "app.services.paper_trading",
    "app.services.market_summary",
    "ui.helpers",
)

_NIFTY_UNIVERSE_DEPENDENTS = (
    "app.services.ingestion",
    "app.services.recommendation_engine",
)


def is_hot_reload_import_error(exc: BaseException) -> bool:
    """True for transient import races during Streamlit module refresh."""
    if isinstance(exc, AttributeError):
        return "'NoneType' object has no attribute '__dict__'" in str(exc)
    if isinstance(exc, KeyError):
        return True
    if isinstance(exc, ImportError):
        msg = str(exc).lower()
        return "partially initialized" in msg or "cannot import name" in msg
    cause = exc.__cause__ or exc.__context__
    return cause is not None and is_hot_reload_import_error(cause)


def import_module_safe(name: str):
    """Import under the reload lock with short retries (fragment vs hot reload)."""
    last: BaseException | None = None
    for attempt in range(5):
        with _reimport_lock:
            try:
                return importlib.import_module(name)
            except (AttributeError, KeyError, ImportError) as exc:
                last = exc
                if not is_hot_reload_import_error(exc):
                    raise
                _purge_module_tree(name)
        time.sleep(0.03 * (attempt + 1))
    if last is not None:
        raise last
    raise ImportError(f"Failed to import {name}")


def _purge_module_tree(name: str) -> None:
    """Drop a module and any cached submodules before a clean reimport."""
    for key in list(sys.modules):
        if key == name or key.startswith(f"{name}."):
            sys.modules.pop(key, None)


def _import_with_sys_modules_stub(name: str):
    """Import with the module kept in sys.modules for the duration of exec.

    Retries at the caller handle Streamlit popping the entry mid-import
    (dataclasses then sees None and raises AttributeError on ``__dict__``).
    """
    spec = importlib.util.find_spec(name)
    if spec is None or spec.loader is None:
        return importlib.import_module(name)

    module = importlib.util.module_from_spec(spec)
    # Re-bind on every attempt so a concurrent pop cannot leave None in place
    # while dataclasses is evaluating annotations.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if sys.modules.get(name) is module:
            sys.modules.pop(name, None)
        raise
    # Streamlit may have replaced/removed our entry during exec — restore it.
    sys.modules[name] = module
    return module


def _purge_app_models() -> None:
    """Drop cached model modules and ORM registry before a clean reimport."""
    base_mod = sys.modules.get(_APP_MODELS_BASE)
    if base_mod is not None and hasattr(base_mod, "Base"):
        base = base_mod.Base
        base.metadata.clear()
        base.registry.dispose()
    for name in (_APP_MODELS, _APP_MODELS_BASE, _APP_MODELS_AUDIT):
        _purge_module_tree(name)


def _models_mappers_healthy() -> bool:
    """True when Instrument ↔ PaperOrder relationships resolve."""
    try:
        from sqlalchemy.orm import class_mapper

        import app.models as models

        class_mapper(models.Instrument)
        class_mapper(models.PaperOrder)
        return True
    except Exception:
        return False


def _refresh_module(name: str) -> None:
    if name in _STATEFUL_UI_MODULES:
        return
    with _reimport_lock:
        if name in _FORCE_REIMPORT:
            _force_reimport_unlocked(name)
            return
        if name not in sys.modules:
            return
        module = sys.modules[name]
        if getattr(module, "__file__", None) is None:
            _force_reimport_unlocked(name)
            return
        try:
            importlib.reload(module)
        except Exception:
            _force_reimport_unlocked(name)


def _force_reimport(name: str):
    with _reimport_lock:
        return _force_reimport_unlocked(name)


def _force_reimport_unlocked(name: str):
    if name == _APP_MODELS:
        _purge_app_models()
    else:
        _purge_module_tree(name)
    last_exc: BaseException | None = None
    for attempt in range(5):
        try:
            return _import_with_sys_modules_stub(name)
        except (KeyError, AttributeError, ImportError) as exc:
            # Streamlit may pop the module mid-import. dataclasses.dataclass
            # needs sys.modules[cls.__module__] to exist (None → AttributeError).
            last_exc = exc
            if not is_hot_reload_import_error(exc) and not isinstance(exc, KeyError):
                raise
            _purge_module_tree(name)
            time.sleep(0.02 * (attempt + 1))
            continue
    assert last_exc is not None
    raise last_exc


def ensure_models_fresh() -> None:
    """Reload app.models when Streamlit cached a pre-migration or broken mapper registry."""
    required = ("PaperTradePlan", "TradePlanStatus", "RecommendationSnapshot", "PaperOrder")
    module = sys.modules.get(_APP_MODELS)
    if module is not None and all(hasattr(module, attr) for attr in required):
        if _models_mappers_healthy():
            return
    with _reimport_lock:
        _force_reimport_unlocked(_APP_MODELS)
        for dependent in _MODEL_DEPENDENTS:
            sys.modules.pop(dependent, None)
        module = sys.modules.get(_APP_MODELS)
    if module is None:
        raise ImportError(f"{_APP_MODELS} failed to import after refresh")
    missing = [attr for attr in required if not hasattr(module, attr)]
    if missing:
        raise ImportError(f"{_APP_MODELS} missing exports after refresh: {missing}")
    if not _models_mappers_healthy():
        raise ImportError(f"{_APP_MODELS} mappers failed to initialize after refresh")


def ensure_market_data_stats_fresh() -> None:
    """Guarantee market_data_stats exports exist (Streamlit can cache stale modules)."""
    required = ("fetch_market_data_stats", "get_market_data_stats", "MarketDataStats")
    module = sys.modules.get(_MARKET_DATA_STATS)
    if module is not None and all(hasattr(module, attr) for attr in required):
        return
    module = _force_reimport(_MARKET_DATA_STATS)
    if not all(hasattr(module, attr) for attr in required):
        module = _force_reimport(_MARKET_DATA_STATS)
    missing = [attr for attr in required if not hasattr(module, attr)]
    if missing:
        raise ImportError(
            f"{_MARKET_DATA_STATS} missing exports after refresh: {missing}"
        )


def _reload_background_jobs_preserving_state():
    """Reload background_jobs code; job state lives in ui.job_registry (unchanged)."""
    import ui.background_jobs as bg

    return importlib.reload(bg)


def ensure_job_api_fresh() -> None:
    """Rebind job_api exports from background_jobs without resetting running jobs."""
    _reload_background_jobs_preserving_state()
    sys.modules.pop(_JOB_API, None)
    importlib.import_module(_JOB_API)


def ensure_live_quotes_fresh() -> None:
    """Guarantee live_quotes exports match on-disk code (background poll thread uses this)."""
    _purge_module_tree("app.providers")
    module = _force_reimport(_LIVE_QUOTES)
    required = ("fetch_live_quotes", "merge_poll_extremes", "merge_session_extremes")
    missing = [attr for attr in required if not hasattr(module, attr)]
    if missing:
        module = _force_reimport(_LIVE_QUOTES)
        missing = [attr for attr in required if not hasattr(module, attr)]
    if missing:
        raise ImportError(f"{_LIVE_QUOTES} missing exports after refresh: {missing}")
    from app.providers.base import QuoteData, SessionQuote

    if "day_open" not in QuoteData.__dataclass_fields__:
        raise ImportError("app.providers.base.QuoteData missing day_open after refresh")
    _ = SessionQuote  # validate importable alongside live_quotes
    sys.modules.pop("app.services.trade_plans", None)


def ensure_intraday_chart_fresh() -> None:
    """Guarantee intraday_chart exports exist (Streamlit can cache stale modules)."""
    sys.modules.pop("ui.position_intraday_chart", None)
    module = _force_reimport(_INTRADAY_CHART)
    required = (
        "DEFAULT_INTERVAL",
        "INTERVAL_OPTIONS",
        "IntradayBar",
        "PositionIntradayContext",
        "resample_intraday_bars",
        "build_position_intraday_context",
    )
    missing = [attr for attr in required if not hasattr(module, attr)]
    if missing:
        module = _force_reimport(_INTRADAY_CHART)
        missing = [attr for attr in required if not hasattr(module, attr)]
    if missing:
        raise ImportError(f"{_INTRADAY_CHART} missing exports after refresh: {missing}")


def ensure_recommendation_helpers_fresh() -> None:
    """Safe import surface for background recommendation jobs."""
    ensure_nifty_universe_fresh()
    module = _force_reimport("ui.recommendation_helpers")
    required = ("run_recommendation_analysis", "run_midday_recommendation_analysis")
    missing = [attr for attr in required if not hasattr(module, attr)]
    if missing:
        raise ImportError(f"ui.recommendation_helpers missing exports after refresh: {missing}")


def ensure_nifty_universe_fresh() -> None:
    """Guarantee nifty_universe exports match on-disk code (daily cache helpers)."""
    required = (
        "ensure_universe_symbols_fresh",
        "is_universe_cache_fresh",
        "universe_cache_refreshed_at",
    )
    module = sys.modules.get(_NIFTY_UNIVERSE)
    if module is not None and all(hasattr(module, attr) for attr in required):
        return
    for dependent in _NIFTY_UNIVERSE_DEPENDENTS:
        sys.modules.pop(dependent, None)
    module = _force_reimport(_NIFTY_UNIVERSE)
    missing = [attr for attr in required if not hasattr(module, attr)]
    if missing:
        module = _force_reimport(_NIFTY_UNIVERSE)
        missing = [attr for attr in required if not hasattr(module, attr)]
    if missing:
        raise ImportError(f"{_NIFTY_UNIVERSE} missing exports after refresh: {missing}")


def ensure_recommendation_engine_fresh() -> None:
    """Reload recommendation_engine before cache/UI dependents import new exports."""
    required = (
        "PatternRanking",
        "StockRecommendation",
        "coerce_stock_recommendation",
        "normalize_recommendation_report",
    )
    module = sys.modules.get(_RECOMMENDATION_ENGINE)
    if module is not None and all(hasattr(module, attr) for attr in required):
        return
    module = _force_reimport(_RECOMMENDATION_ENGINE)
    missing = [attr for attr in required if not hasattr(module, attr)]
    if missing:
        module = _force_reimport(_RECOMMENDATION_ENGINE)
        missing = [attr for attr in required if not hasattr(module, attr)]
    if missing:
        raise ImportError(
            f"{_RECOMMENDATION_ENGINE} missing exports after refresh: {missing}"
        )


def ensure_recommendation_cache_fresh() -> None:
    """Guarantee recommendation_cache exports match on-disk code (Streamlit caches stale modules)."""
    ensure_recommendation_engine_fresh()
    required = (
        "load_cached_recommendations",
        "save_recommendation_snapshot",
        "load_midday_cached_recommendations_for_ui",
        "save_midday_recommendation_snapshot",
    )
    module = sys.modules.get(_RECOMMENDATION_CACHE)
    if module is not None and all(hasattr(module, attr) for attr in required):
        import inspect

        sig = inspect.signature(module.load_cached_recommendations)
        if "prediction_date" in sig.parameters:
            return
    module = _force_reimport(_RECOMMENDATION_CACHE)
    missing = [attr for attr in required if not hasattr(module, attr)]
    if missing:
        module = _force_reimport(_RECOMMENDATION_CACHE)
        missing = [attr for attr in required if not hasattr(module, attr)]
    if missing:
        raise ImportError(f"{_RECOMMENDATION_CACHE} missing exports after refresh: {missing}")
    import inspect

    sig = inspect.signature(module.load_cached_recommendations)
    if "prediction_date" not in sig.parameters:
        raise ImportError(
            f"{_RECOMMENDATION_CACHE}.load_cached_recommendations missing prediction_date — "
            "restart Streamlit if this persists"
        )


def ensure_budget_portfolio_fresh() -> None:
    """Guarantee budget_portfolio exports match on-disk code (Streamlit caches stale modules)."""
    module = _force_reimport(_BUDGET_PORTFOLIO)
    required = (
        "compute_budget_view",
        "compute_base_budget_available",
        "portfolio_total_at_cost",
        "portfolio_total_with_unrealized",
    )
    missing = [attr for attr in required if not hasattr(module, attr)]
    if missing:
        module = _force_reimport(_BUDGET_PORTFOLIO)
        missing = [attr for attr in required if not hasattr(module, attr)]
    if missing:
        raise ImportError(f"{_BUDGET_PORTFOLIO} missing exports after refresh: {missing}")


def ensure_defaults_fresh() -> None:
    """Guarantee app.defaults exports match on-disk code (Streamlit caches stale modules)."""
    module = _force_reimport(_APP_DEFAULTS)
    required = (
        "DEFAULT_GST_RATE",
        "DEFAULT_ZERODHA_DP_CHARGE_PER_SCRIP_INR",
        "DEFAULT_BROKERAGE_MIN_PER_SHARE_INR",
        "DEFAULT_EXCHANGE_TXN_RATE",
        "DEFAULT_SEBI_TURNOVER_RATE",
    )
    missing = [attr for attr in required if not hasattr(module, attr)]
    if missing:
        module = _force_reimport(_APP_DEFAULTS)
        missing = [attr for attr in required if not hasattr(module, attr)]
    if missing:
        raise ImportError(f"{_APP_DEFAULTS} missing exports after refresh: {missing}")


def ensure_applicable_rates_fresh() -> None:
    """Guarantee applicable_rates exports match on-disk code (Streamlit caches stale modules)."""
    from app.services.applicable_rates import reset_applicable_rates_cache

    reset_applicable_rates_cache()
    module = _force_reimport(_APPLICABLE_RATES)
    required = (
        "ApplicableRates",
        "get_applicable_rates",
        "reset_applicable_rates_cache",
    )
    missing = [attr for attr in required if not hasattr(module, attr)]
    if missing:
        module = _force_reimport(_APPLICABLE_RATES)
        missing = [attr for attr in required if not hasattr(module, attr)]
    if missing:
        raise ImportError(f"{_APPLICABLE_RATES} missing exports after refresh: {missing}")
    rates_cls = module.ApplicableRates
    if "brokerage_min_per_share_inr" not in getattr(rates_cls, "__dataclass_fields__", {}):
        raise ImportError(
            f"{_APPLICABLE_RATES}.ApplicableRates missing brokerage_min_per_share_inr — "
            "restart Streamlit if this persists"
        )


def ensure_trade_tax_fresh() -> None:
    """Guarantee trade_tax dual-broker exports match on-disk code (Streamlit caches stale modules)."""
    ensure_defaults_fresh()
    ensure_applicable_rates_fresh()
    required = (
        "DualBrokerRealizedPnlSummary",
        "RealizedPnlAfterTaxSummary",
        "summarize_sell_trades_dual_broker",
    )
    module = sys.modules.get(_TRADE_TAX)
    if module is not None and all(hasattr(module, attr) for attr in required):
        return
    # Locked stub-import (retries dataclass sys.modules races) — never purge+import
    # outside the lock; that triggered AttributeError on @dataclass.
    _force_reimport(_BROKER_DELIVERY_PROFILES)
    module = _force_reimport(_TRADE_TAX)
    missing = [attr for attr in required if not hasattr(module, attr)]
    if missing:
        _force_reimport(_BROKER_DELIVERY_PROFILES)
        module = _force_reimport(_TRADE_TAX)
        missing = [attr for attr in required if not hasattr(module, attr)]
    if missing:
        raise ImportError(f"{_TRADE_TAX} missing exports after refresh: {missing}")


def ensure_fresh_ui_modules() -> None:
    """Rebind UI exports after code changes without restarting Streamlit."""
    last: BaseException | None = None
    for attempt in range(3):
        try:
            ensure_models_fresh()
            ensure_defaults_fresh()
            ensure_applicable_rates_fresh()
            ensure_nifty_universe_fresh()
            ensure_market_data_stats_fresh()
            ensure_budget_portfolio_fresh()
            ensure_trade_tax_fresh()
            ensure_live_quotes_fresh()
            _force_reimport(_BUDGET_ALLOCATOR)
            ensure_intraday_chart_fresh()
            ensure_recommendation_cache_fresh()
            ensure_job_api_fresh()
            import_module_safe(_TRADE_PLANS)
            for name in _UI_MODULE_CHAIN:
                if name in _FORCE_REIMPORT:
                    continue
                _refresh_module(name)
            return
        except (AttributeError, KeyError, ImportError) as exc:
            last = exc
            if not is_hot_reload_import_error(exc):
                raise
            time.sleep(0.05 * (attempt + 1))
    if last is not None:
        raise last

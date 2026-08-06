"""Canonical API surface for post-deployment smoke tests."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ApiEndpoint:
    method: str
    path: str
    name: str
    allowed_statuses: frozenset[int] = frozenset({200})
    json_body: dict | None = None
    skip_by_default: bool = False
    note: str = ""


# Read-only GET endpoints exercised after every deploy (must not return 5xx).
GET_ENDPOINTS: tuple[ApiEndpoint, ...] = (
    ApiEndpoint("GET", "/health", "health"),
    ApiEndpoint("GET", "/api/instruments", "list_instruments"),
    ApiEndpoint("GET", "/api/instruments/NIFTY50/candles", "nifty50_candles"),
    ApiEndpoint("GET", "/api/instruments/NIFTY50/candles?days=7", "nifty50_candles_7d"),
    ApiEndpoint("GET", "/api/market/summary", "market_summary"),
    ApiEndpoint("GET", "/api/paper/account", "paper_account"),
    ApiEndpoint("GET", "/api/paper/positions", "paper_positions"),
    ApiEndpoint("GET", "/api/paper/orders", "paper_orders"),
    ApiEndpoint("GET", "/api/paper/trades", "paper_trades"),
    ApiEndpoint("GET", "/api/backtest/patterns", "backtest_patterns"),
    ApiEndpoint("GET", "/api/backtest/latest", "backtest_latest"),
    ApiEndpoint(
        "GET",
        "/api/backtest/999999",
        "backtest_missing_run",
        allowed_statuses=frozenset({404}),
        note="missing run id should 404, not 5xx",
    ),
    ApiEndpoint(
        "GET",
        "/api/backtest/999999/patterns/missing_pattern/detail",
        "pattern_detail_missing",
        allowed_statuses=frozenset({404}),
        note="missing run/pattern should 404, not 5xx",
    ),
    ApiEndpoint("GET", "/api/admin/audit-logs", "audit_logs"),
    ApiEndpoint("GET", "/api/admin/audit-logs?limit=5", "audit_logs_limited"),
)

# Mutating endpoints — optional (slow or side effects). Still must not 5xx when enabled.
MUTATING_ENDPOINTS: tuple[ApiEndpoint, ...] = (
    ApiEndpoint(
        "POST",
        "/api/backtest/run",
        "backtest_run",
        allowed_statuses=frozenset({200, 400}),
        skip_by_default=True,
        note="400 acceptable when candle data insufficient",
    ),
    ApiEndpoint(
        "POST",
        "/api/admin/sync",
        "admin_sync",
        allowed_statuses=frozenset({200}),
        skip_by_default=True,
        note="slow — hits NSE; enable with POST_DEPLOY_RUN_MUTATING=1",
    ),
    ApiEndpoint(
        "POST",
        "/api/paper/orders",
        "paper_place_order",
        allowed_statuses=frozenset({200, 400}),
        skip_by_default=True,
        json_body={
            "symbol": "NIFTY50",
            "side": "BUY",
            "order_type": "MARKET",
            "quantity": 1,
        },
        note="optional write test",
    ),
    ApiEndpoint(
        "DELETE",
        "/api/paper/orders/999999",
        "paper_cancel_missing_order",
        allowed_statuses=frozenset({400, 404}),
        skip_by_default=True,
        note="missing order should not 5xx",
    ),
)

ALL_ENDPOINTS: tuple[ApiEndpoint, ...] = GET_ENDPOINTS + MUTATING_ENDPOINTS

UI_MODULES: tuple[str, ...] = (
    "ui.dashboard",
    "ui.helpers",
    "ui.async_runner",
    "ui.background_jobs",
    "ui.job_api",
    "ui.backtest_display",
    "ui.recommendations_display",
    "ui.recommendation_helpers",
    "ui.streamlit_imports",
    "ui.ui_load_harness",
)

UI_PAGE_RENDERERS: tuple[str, ...] = (
    "render_trading_page",
    "render_backtest_page",
    "render_recommendations_page",
    "render_midday_recommendations_page",
    "render_eod_analysis_page",
    "render_paper_trading_trend_page",
    "main",
)

APP_MODULES: tuple[str, ...] = (
    "app.main",
    "app.api.routes.market",
    "app.api.routes.backtest",
    "app.services.market_data_stats",
    "app.services.paper_trading",
    "app.services.backtest",
    "app.services.ingestion",
    "app.services.simulation_cache",
    "app.db.session",
    "app.db.ui_session",
)

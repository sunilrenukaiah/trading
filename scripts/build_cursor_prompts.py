#!/usr/bin/env python3
"""Generate self-contained Cursor regeneration prompts (no PDFs, no zip required)."""

from __future__ import annotations

import textwrap
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "cursor-regeneration-prompts"

SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", "regeneration-pack", "cursor-regeneration-prompts", "backups", "node_modules"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".log", ".sql", ".ttf", ".png"}
SKIP_NAMES = {"build_regeneration_pack.py", "build_cursor_prompts.py"}


def should_include(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    if any(p in SKIP_DIRS for p in rel.parts):
        return False
    if path.suffix in SKIP_SUFFIXES:
        return False
    if path.name in SKIP_NAMES:
        return False
    return True


def read_file(rel: str) -> str:
    path = ROOT / rel
    if not path.exists():
        raise FileNotFoundError(rel)
    return path.read_text(encoding="utf-8")


def read_lines(rel: str, start: int, end: int) -> str:
    """Return 1-based inclusive line range from a file."""
    lines = read_file(rel).splitlines(keepends=True)
    return "".join(lines[start - 1 : end])


def lang_for(rel: str) -> str:
    ext = Path(rel).suffix.lstrip(".") or "text"
    return {
        "py": "python", "json": "json", "toml": "toml", "ini": "ini",
        "md": "markdown", "bat": "batch", "sh": "bash", "yml": "yaml", "yaml": "yaml",
    }.get(ext, ext)


def embed_files(file_paths: list[str]) -> str:
    chunks: list[str] = []
    for rel in sorted(file_paths):
        content = read_file(rel)
        chunks.append(f"#### File: `{rel}`\n\n```{lang_for(rel)}\n{content}```\n")
    return "\n".join(chunks)


def embed_file_slice(rel: str, start: int, end: int, *, label: str | None = None) -> str:
    content = read_lines(rel, start, end)
    tag = label or f"lines {start}–{end}"
    return f"#### File: `{rel}` ({tag})\n\n```{lang_for(rel)}\n{content}```\n"


def write_phase(
    filename: str,
    phase_label: str,
    title: str,
    prerequisites: list[str],
    task: str,
    body_content: str,
    verification: list[str],
    notes: str = "",
) -> tuple[int, int]:
    prereq = "\n".join(f"- {p}" for p in prerequisites) if prerequisites else "- None (start here)"
    verify = "\n".join(f"- [ ] {v}" for v in verification)
    template = f"""\
# {phase_label}: {title}

> **How to use:** Open Cursor Agent on your Windows machine. Create an empty folder
> `C:\\Users\\<you>\\projects\\trading`. Paste this entire file as your prompt.
> Tell Cursor: *"Create every file below exactly as specified. Do not skip or summarize."*

---

## Cursor Agent Prompt (copy from here)

You are rebuilding the **NIFTY Paper Trading Simulation Platform** on Windows from this
specification alone. There is no Git repo, no zip, and no PDFs — only the content in
this prompt.

### Prerequisites from earlier phases
{prereq}

### {phase_label} goal
{task}

{notes}

### Critical rules
1. Create each file at the **exact relative path** shown (repo root = `trading/`).
2. Copy file contents **verbatim** from the code blocks below — no omissions, no invented code.
3. Preserve imports, function names, session-state keys, and Streamlit fragment patterns.
4. After creating files, list every path you wrote and confirm line counts match.

{body_content}

### Verification checklist
{verify}

---

*End of {phase_label} prompt.*
"""
    (OUT / filename).write_text(template, encoding="utf-8")
    return len(template.encode()), body_content.count("#### File:")


def write_files_phase(phase: dict) -> tuple[int, int, int]:
    body = "### Files to create in this phase\n\n" + embed_files(phase["paths"])
    nbytes, nfiles = write_phase(
        phase["file"],
        phase["label"],
        phase["title"],
        phase["prerequisites"],
        phase["task"],
        body,
        phase["verification"],
        phase.get("notes", ""),
    )
    size = sum((ROOT / p).stat().st_size for p in phase["paths"])
    return nbytes, nfiles, size


def write_dashboard_split_phase(phase: dict) -> tuple[int, int, int]:
    rel = phase["dashboard_path"]
    start, end = phase["line_range"]
    merge = phase["merge_instruction"]
    body = f"""\
### Dashboard merge instruction
{merge}

### File content

{embed_file_slice(rel, start, end)}
"""
    nbytes, nfiles = write_phase(
        phase["file"],
        phase["label"],
        phase["title"],
        phase["prerequisites"],
        phase["task"],
        body,
        phase["verification"],
        phase.get("notes", ""),
    )
    size = len(read_lines(rel, start, end).encode())
    return nbytes, 1, size


# ── Standard file phases ──────────────────────────────────────────────────────

STANDARD_PHASES: list[dict] = [
    {
        "file": "01-phase-root-setup.prompt.md",
        "label": "Phase 01",
        "title": "Root setup, migration scripts, and Windows launchers",
        "prerequisites": ["Empty folder opened as Cursor workspace root"],
        "task": "Create project root files, one-time setup (`Setup.py`), startup scripts, and Windows batch helpers.",
        "paths": [
            "Setup.py", "README.md",
            "requirements-migrate.txt", "requirements-start.txt", "requirements-start-lab.txt",
            "setup.bat", "setup-pycharm.bat", "start.bat",
            "scripts/run_app.py", "scripts/migrate_checklist.py", "scripts/startup_checklist.py",
            "scripts/ide_setup.py", "scripts/platform_utils.py", "scripts/instance_guard.py",
            "scripts/pycharm_run_fastapi.py",
            "scripts/backup_database.sh", "scripts/recover_docker_database.sh", "scripts/restore_database.sh",
            "docs/MIGRATION.md", "docs/PYCHARM.md",
        ],
        "verification": [
            "All listed files exist at correct paths",
            "`python Setup.py` runs without import errors (after Phase 02 venv exists)",
        ],
    },
    {
        "file": "02-phase-backend-config-db.prompt.md",
        "label": "Phase 02",
        "title": "Backend config, Alembic migrations, bootstrap",
        "prerequisites": ["Phase 01 complete"],
        "task": "Create backend package config, environment template, Alembic migrations 001–007, and app bootstrap.",
        "paths": [
            "backend/pyproject.toml", "backend/env.example", "backend/alembic.ini", "backend/alembic/env.py",
            "backend/alembic/versions/001_initial_schema.py", "backend/alembic/versions/002_backtest_results.py",
            "backend/alembic/versions/003_daily_simulation_cache.py", "backend/alembic/versions/004_audit_logs.py",
            "backend/alembic/versions/005_paper_trade_plans.py", "backend/alembic/versions/006_recommendation_snapshots.py",
            "backend/alembic/versions/007_trade_plan_time_exit.py",
            "backend/app/__init__.py", "backend/app/config.py", "backend/app/defaults.py",
            "backend/app/logging_setup.py", "backend/app/bootstrap.py", "backend/app/main.py",
            "backend/app/db/__init__.py", "backend/app/db/session.py", "backend/app/db/ui_session.py",
            "backend/app/db/lab_schema.py", "backend/scripts/run_tests.sh",
        ],
        "verification": [
            "`cd backend && python -m venv .venv` then `.venv\\Scripts\\pip install -e .`",
            "Copy `backend\\env.example` to `backend\\.env`",
            "PostgreSQL: user `trading`, db `trading`, password `trading`",
            "`cd backend && .venv\\Scripts\\alembic upgrade head` succeeds",
        ],
    },
    {
        "file": "03-phase-models-schemas.prompt.md",
        "label": "Phase 03",
        "title": "SQLAlchemy models and Pydantic schemas",
        "prerequisites": ["Phase 02 complete — DB migrations applied"],
        "task": "Create ORM models (instruments, candles, paper trading, backtest, recommendations, audit) and API schemas.",
        "paths": [
            "backend/app/models/__init__.py", "backend/app/models/base.py", "backend/app/models/audit_log.py",
            "backend/app/schemas/__init__.py", "backend/app/schemas/audit.py", "backend/app/schemas/backtest.py",
        ],
        "verification": [
            "`cd backend && .venv\\Scripts\\python -c \"from app.models import Instrument, PaperAccount\"`",
        ],
    },
    {
        "file": "04-phase-providers-ingestion.prompt.md",
        "label": "Phase 04",
        "title": "Market data providers and ingestion services",
        "prerequisites": ["Phase 03 complete"],
        "task": "Create pluggable providers (NSE, yfinance, Sharekhan stub), ingestion, calendar, universe, and OHLCV utilities.",
        "paths": [
            "backend/app/providers/__init__.py", "backend/app/providers/base.py",
            "backend/app/providers/nse_provider.py", "backend/app/providers/yfinance_provider.py",
            "backend/app/providers/sharekhan_provider.py",
            "backend/app/services/ingestion.py", "backend/app/services/market_calendar.py",
            "backend/app/services/nifty_universe.py", "backend/app/services/nifty250_index.py",
            "backend/app/services/ohlcv_utils.py", "backend/app/services/candle_quality.py",
            "backend/app/services/market_data_stats.py", "backend/app/services/market_summary.py",
            "backend/app/services/live_quotes.py", "backend/app/services/intraday_chart.py",
            "backend/app/services/applicable_rates.py",
            "backend/app/jobs/__init__.py", "backend/app/jobs/sync_market_data.py",
            "backend/app/jobs/refresh_applicable_rates.py", "backend/app/jobs/reconcile_session_targets.py",
        ],
        "verification": ["`cd backend && .venv\\Scripts\\python -m app.bootstrap` seeds instruments"],
    },
    {
        "file": "05-phase-paper-trading.prompt.md",
        "label": "Phase 05",
        "title": "Paper trading, trade plans, brackets, tax",
        "prerequisites": ["Phase 04 complete"],
        "task": "Create paper trading engine, bracket orders, trade plans, square-off at 3:25 PM IST, retention, and broker tax profiles.",
        "paths": [
            "backend/app/services/paper_trading.py", "backend/app/services/trade_plans.py",
            "backend/app/services/bracket_utils.py", "backend/app/services/bracket_reconcile_state.py",
            "backend/app/services/broker_delivery_profiles.py", "backend/app/services/trade_tax.py",
            "backend/app/services/paper_trading_retention.py", "backend/app/services/paper_trading_trend.py",
            "backend/app/services/eod_trade_analysis.py",
        ],
        "verification": [
            "Import: `from app.services.paper_trading import PaperTradingService`",
            "Import: `from app.services.trade_plans import TradePlanService`",
        ],
    },
    {
        "file": "06-phase-patterns-backtest.prompt.md",
        "label": "Phase 06",
        "title": "Pattern registry (79 patterns) and backtest engine",
        "prerequisites": ["Phase 05 complete"],
        "task": "Create pattern implementations, registry, indicators, backtest engine, simulation cache, and pattern definitions service.",
        "paths": [
            "backend/app/strategies/__init__.py", "backend/app/strategies/base.py",
            "backend/app/strategies/indicators.py", "backend/app/strategies/registry.py",
            "backend/app/strategies/patterns/__init__.py", "backend/app/strategies/patterns/bollinger.py",
            "backend/app/strategies/patterns/candlestick.py", "backend/app/strategies/patterns/chart_patterns.py",
            "backend/app/strategies/patterns/combinations.py", "backend/app/strategies/patterns/fidelity_candlestick.py",
            "backend/app/strategies/patterns/fidelity_indicators.py", "backend/app/strategies/patterns/groww_candlestick.py",
            "backend/app/strategies/patterns/price_action.py", "backend/app/strategies/patterns/technical.py",
            "backend/app/services/backtest.py", "backend/app/services/backtest_loader.py",
            "backend/app/services/simulation_cache.py", "backend/app/services/pattern_definitions.py",
            "backend/app/services/pattern_examples.py",
        ],
        "verification": [
            "`cd backend && .venv\\Scripts\\python -c \"from app.strategies.registry import list_patterns; assert len(list_patterns()) >= 79\"`",
        ],
    },
    {
        "file": "07-phase-recommendations.prompt.md",
        "label": "Phase 07",
        "title": "Recommendation engine, midday analysis, budget allocation",
        "prerequisites": ["Phase 06 complete"],
        "task": "Create recommendation engine (30-day lookback/eval), cache, budget allocator/portfolio, midday sync and recommendations.",
        "paths": [
            "backend/app/services/recommendation_engine.py", "backend/app/services/recommendation_cache.py",
            "backend/app/services/budget_allocator.py", "backend/app/services/budget_portfolio.py",
            "backend/app/services/midday_recommendations.py", "backend/app/services/midday_market_sync.py",
        ],
        "verification": ["Import: `from app.services.recommendation_engine import RecommendationEngine`"],
    },
    {
        "file": "08-phase-api-audit.prompt.md",
        "label": "Phase 08",
        "title": "FastAPI routes, audit system, middleware",
        "prerequisites": ["Phase 07 complete"],
        "task": "Create optional FastAPI REST layer, audit backends (postgres/composite/logging), middleware, and app logger.",
        "paths": [
            "backend/app/api/__init__.py", "backend/app/api/routes/__init__.py",
            "backend/app/api/routes/market.py", "backend/app/api/routes/backtest.py",
            "backend/app/middleware/__init__.py", "backend/app/middleware/audit.py",
            "backend/app/services/__init__.py", "backend/app/services/app_logger.py",
            "backend/app/services/audit.py", "backend/app/services/audit_types.py",
            "backend/app/services/audit_handlers.py", "backend/app/services/audit_dispatch.py",
            "backend/app/services/audit_decorators.py",
            "backend/app/services/audit_backends/__init__.py", "backend/app/services/audit_backends/base.py",
            "backend/app/services/audit_backends/composite.py", "backend/app/services/audit_backends/logging_backend.py",
            "backend/app/services/audit_backends/noop.py", "backend/app/services/audit_backends/postgres.py",
            "backend/app/services/audit_backends/registry.py", "backend/app/services/audit_backends/serializers.py",
        ],
        "verification": [
            "`cd backend && .venv\\Scripts\\uvicorn app.main:app --port 8000` starts (optional)",
            "GET http://localhost:8000/docs loads",
        ],
    },
    {
        "file": "09-phase-streamlit-ui-core.prompt.md",
        "label": "Phase 09",
        "title": "Streamlit UI modules (except dashboard.py)",
        "prerequisites": ["Phase 08 complete"],
        "task": (
            "Create all Streamlit helper modules: async_runner, background_jobs, live quote poller, "
            "display modules, chart dialogs, scheduled sync/rates, tab-switch audit."
        ),
        "notes": textwrap.dedent("""\
            ### Streamlit critical patterns (must preserve)
            - `ui/async_runner.py`: ONE background loop via `run_async()`; never `asyncio.run()` from Streamlit.
            - `ui/background_jobs.py`: `JobKind` enum; fragment polling; **never** `st.rerun()` right after job start.
            - **Never** call `_render_trading_page_body()` from a fragment that writes sidebar.
            - Market sync: `_market_sync_progress_fragment()` — progress only, no body.
            - Recommendations/mid-day: `_rec_live_poll` / `_midday_live_poll` flags.
            - Lazy loading: `st.radio` sections — not `st.tabs`.
            - `streamlit_imports.py`: hot-reload purges for models, defaults, trade_tax, live_quotes.
            """),
        "paths": [
            "backend/ui/__init__.py", "backend/ui/streamlit_imports.py", "backend/ui/async_runner.py",
            "backend/ui/background_jobs.py", "backend/ui/job_registry.py", "backend/ui/job_api.py",
            "backend/ui/instance_guard.py", "backend/ui/helpers.py", "backend/ui/positions_display.py",
            "backend/ui/orders_display.py", "backend/ui/backtest_display.py",
            "backend/ui/recommendations_display.py", "backend/ui/recommendation_helpers.py",
            "backend/ui/recommendation_chart.py", "backend/ui/midday_recommendations_display.py",
            "backend/ui/eod_analysis_display.py", "backend/ui/paper_trading_trend_display.py",
            "backend/ui/pattern_definitions_display.py", "backend/ui/pattern_definition_chart.py",
            "backend/ui/symbol_history_chart.py", "backend/ui/position_intraday_chart.py",
            "backend/ui/live_quote_poller.py", "backend/ui/scheduled_market_sync.py",
            "backend/ui/scheduled_rates_refresh.py", "backend/ui/tab_switch_audit.py",
        ],
        "verification": [
            "`cd backend && .venv\\Scripts\\python -c \"from ui.async_runner import run_async; from ui.background_jobs import JobKind\"`",
        ],
    },
    {
        "file": "11-phase-data-json.prompt.md",
        "label": "Phase 11",
        "title": "Static JSON data files",
        "prerequisites": ["Phase 10c complete — dashboard.py fully assembled"],
        "task": "Create all JSON config/data files under backend/app/data/.",
        "paths": [
            "backend/app/data/nifty50.json", "backend/app/data/recommendation_universe.json",
            "backend/app/data/backtest_universe.json", "backend/app/data/pattern_definitions.json",
            "backend/app/data/nse_trading_holidays.json", "backend/app/data/applicable_rates.json",
            "backend/app/data/bracket_reconcile_state.json", "backend/app/data/midday_recommendation_snapshot.json",
            "backend/app/data/nifty_universe_cache.json",
        ],
        "verification": [
            "Pattern definitions page loads catalog",
            "NIFTY250 universe resolves in Trading tab",
        ],
    },
    {
        "file": "13-phase-architecture-docs.prompt.md",
        "label": "Phase 13",
        "title": "Architecture documentation (optional but recommended)",
        "prerequisites": ["Phase 12d complete"],
        "task": "Create docs/project-architecture/ reference documentation.",
        "paths": sorted(str(p.relative_to(ROOT)) for p in (ROOT / "docs" / "project-architecture").rglob("*.md")),
        "verification": ["docs/project-architecture/README.md exists with index table"],
    },
    {
        "file": "14-phase-ci-optional.prompt.md",
        "label": "Phase 14 (optional)",
        "title": "CI/CD pipeline configs",
        "prerequisites": ["Phase 12d complete — app runs and tests pass"],
        "task": "Optional: GitLab CI and GitHub Actions integration test workflows.",
        "paths": [".gitlab-ci.yml", ".github/workflows/integration-tests.yml"],
        "verification": ["Pipeline YAML files parse without syntax errors"],
    },
]

# ── Dashboard split (Phase 10a–10c) ───────────────────────────────────────────

DASHBOARD_SPLITS: list[dict] = [
    {
        "file": "10a-phase-dashboard-part1-trading.prompt.md",
        "label": "Phase 10a",
        "title": "dashboard.py Part 1 — Trading page, live polling, market sync",
        "prerequisites": ["Phase 09 complete — all ui/*.py except dashboard exist"],
        "task": (
            "Create `backend/ui/dashboard.py` with Part 1 (lines 1–1217): imports, `_init_app`, "
            "trading page (`render_trading_page`, `_render_trading_page_body`), live polling fragments, "
            "market sync progress fragment, positions/orders/trades/NIFTY250 tabs, sidebar place order."
        ),
        "dashboard_path": "backend/ui/dashboard.py",
        "line_range": (1, 1217),
        "merge_instruction": (
            "**CREATE** `backend/ui/dashboard.py` with the content below. "
            "This is Part 1 of 3. Parts 10b and 10c will append to this file."
        ),
        "notes": textwrap.dedent("""\
            ### Behaviors in Part 1 (verify after merge)
            - `@st.fragment` live polling every 10s on Positions (`_positions_auto_refresh_body`)
            - `@st.fragment` market sync progress (`_market_sync_progress_fragment`) — **no body render inside**
            - `_market_sync_requested` session flag on Refresh market data button
            - Trading data radio: Positions / Orders / Trades / NIFTY250 (lazy load)
            - Footer radio: Summary only / Market data & simulation
            - Manual bracket reconcile button (not auto on page load)
            - `@st.dialog` chart popups with `on_dismiss` session cleanup
            - `_ensure_recommendation_session_state()` for NIFTY250 highlights
            """),
        "verification": [
            "File exists with 1217 lines",
            "Contains `render_trading_page` and `_market_sync_progress_fragment`",
            "Does NOT yet contain `render_recommendations_page` (that's Part 2)",
        ],
    },
    {
        "file": "10b-phase-dashboard-part2-backtest-recs.prompt.md",
        "label": "Phase 10b",
        "title": "dashboard.py Part 2 — Backtest, Recommendations, budget simulation",
        "prerequisites": ["Phase 10a complete — dashboard.py Part 1 exists (1217 lines)"],
        "task": (
            "Append Part 2 (lines 1218–2388) to `backend/ui/dashboard.py`: backtest page, "
            "recommendations page with lazy sections, budget simulation, live recommendation fragment."
        ),
        "dashboard_path": "backend/ui/dashboard.py",
        "line_range": (1218, 2388),
        "merge_instruction": (
            "**APPEND** the content below to the END of existing `backend/ui/dashboard.py`. "
            "Do not duplicate Part 1 imports. After append, file should have ~2388 lines."
        ),
        "notes": textwrap.dedent("""\
            ### Behaviors in Part 2
            - `render_backtest_page()` with section radio: Today's validation / 30-day simulation
            - `SIM_BACKTEST` and `TODAY_PREDICTION` background jobs
            - `render_recommendations_page()` with section radio: Stock picks / Budget & orders / Budget simulation
            - `_recommendations_live_fragment()` with `_rec_live_poll` flag (no blank page)
            - `_render_budget_simulation_section()` read-only what-if (separate `rec_sim_budget` key)
            - Place trade / Place order for all with bracket validation
            """),
        "verification": [
            "dashboard.py now ~2388 lines",
            "Contains `render_backtest_page` and `_recommendations_live_fragment`",
            "Does NOT yet contain `main()` (that's Part 3)",
        ],
    },
    {
        "file": "10c-phase-dashboard-part3-midday-eod-main.prompt.md",
        "label": "Phase 10c",
        "title": "dashboard.py Part 3 — Mid-day, EOD, Paper trend, main()",
        "prerequisites": ["Phase 10b complete — dashboard.py ~2388 lines"],
        "task": (
            "Append Part 3 (lines 2389–3062) to `backend/ui/dashboard.py`: mid-day analysis page, "
            "EOD page, paper trading trend, scheduled sync ticks, sidebar navigation, `main()`."
        ),
        "dashboard_path": "backend/ui/dashboard.py",
        "line_range": (2389, 3062),
        "merge_instruction": (
            "**APPEND** the content below to the END of existing `backend/ui/dashboard.py`. "
            "Final file must be exactly 3062 lines with `if __name__ == \"__main__\": main()` at the end."
        ),
        "notes": textwrap.dedent("""\
            ### Behaviors in Part 3
            - `render_midday_recommendations_page()` — 11:45 AM–4:30 PM IST run window
            - `_midday_recommendations_live_fragment()` with `_midday_live_poll` flag
            - Mid-day section radio: Analysis / Place orders (lazy place-order DB work)
            - `render_eod_analysis_page()` — manual Refresh EOD; NIFTY250 missed profitable after 3:45 PM
            - `render_paper_trading_trend_page()` — Sharekhan vs Zerodha after-tax comparison
            - `_scheduled_market_sync_tick()` at 3:45 PM and 6:00 PM IST
            - Sidebar: 7-page radio nav, Refresh market data, background job watcher
            """),
        "verification": [
            "dashboard.py is exactly 3062 lines",
            "`streamlit run ui/dashboard.py` starts without syntax errors",
            "All 7 sidebar pages render; job buttons do not cause blank page",
        ],
    },
]

# ── Test splits (Phase 12a–12d) ───────────────────────────────────────────────

TEST_GROUPS: dict[str, list[str]] = {
    "12a-phase-tests-core-config.prompt.md": [
        "backend/tests/conftest.py",
        "backend/tests/integration/test_config_contract.py",
        "backend/tests/integration/test_module_imports.py",
        "backend/tests/integration/test_dashboard_import_contract.py",
        "backend/tests/integration/test_async_runner_db.py",
        "backend/tests/integration/test_background_jobs.py",
        "backend/tests/integration/test_instance_guard.py",
        "backend/tests/integration/test_market_calendar.py",
        "backend/tests/integration/test_patterns_registry.py",
        "backend/tests/integration/test_pattern_definitions.py",
        "backend/tests/integration/test_fastapi_smoke.py",
        "backend/tests/post_deploy/conftest.py",
        "backend/tests/post_deploy/api_catalog.py",
        "backend/tests/post_deploy/test_api_smoke.py",
        "backend/tests/post_deploy/test_ui_smoke.py",
    ],
    "12b-phase-tests-market-paper-audit.prompt.md": [
        "backend/tests/integration/test_ingestion.py",
        "backend/tests/integration/test_candle_quality.py",
        "backend/tests/integration/test_nifty_universe.py",
        "backend/tests/integration/test_nifty250_index.py",
        "backend/tests/integration/test_market_data_stats.py",
        "backend/tests/integration/test_simulation_cache.py",
        "backend/tests/integration/test_simulation_backfill.py",
        "backend/tests/integration/test_applicable_rates.py",
        "backend/tests/integration/test_live_quotes.py",
        "backend/tests/integration/test_paper_trading_retention.py",
        "backend/tests/integration/test_paper_trading_square_off.py",
        "backend/tests/integration/test_paper_trading_trend.py",
        "backend/tests/integration/test_session_scoped_trading.py",
        "backend/tests/integration/test_trade_tax.py",
        "backend/tests/integration/test_audit.py",
        "backend/tests/integration/test_audit_backends.py",
        "backend/tests/integration/test_audit_catalog.py",
        "backend/tests/integration/test_audit_error_handling.py",
        "backend/tests/integration/test_regression_from_audit.py",
        "backend/tests/integration/test_bracket_reconcile_state.py",
    ],
    "12c-phase-tests-recommendations-brackets.prompt.md": [
        "backend/tests/integration/test_recommendation_engine.py",
        "backend/tests/integration/test_recommendation_cache.py",
        "backend/tests/integration/test_budget_allocator.py",
        "backend/tests/integration/test_budget_portfolio.py",
        "backend/tests/integration/test_midday_recommendations.py",
        "backend/tests/integration/test_midday_market_sync.py",
        "backend/tests/integration/test_midday_budget.py",
        "backend/tests/integration/test_trade_plans.py",
        "backend/tests/integration/test_allocation_trade_plan_state.py",
        "backend/tests/integration/test_eod_trade_analysis.py",
    ],
    "12d-phase-tests-ui-display-contracts.prompt.md": [
        "backend/tests/integration/test_trading_page_ui_contract.py",
        "backend/tests/integration/test_lazy_loading.py",
        "backend/tests/integration/test_ui_contract.py",
        "backend/tests/integration/test_tab_switch_audit.py",
        "backend/tests/integration/test_positions_display.py",
        "backend/tests/integration/test_position_intraday_chart.py",
        "backend/tests/integration/test_symbol_history_chart.py",
        "backend/tests/integration/test_live_quote_poller.py",
        "backend/tests/integration/test_recommendations_display.py",
        "backend/tests/integration/test_recommendation_chart.py",
    ],
}

TEST_PHASE_META = {
    "12a-phase-tests-core-config.prompt.md": {
        "label": "Phase 12a", "num": "12a",
        "title": "Tests — core, config, imports, smoke",
        "prerequisites": ["Phases 01–11 complete"],
        "task": "Create conftest, config/import contracts, async_runner, background_jobs, patterns registry, post-deploy smoke tests.",
        "verification": ["`cd backend && .venv\\Scripts\\python -m pytest tests/integration/test_config_contract.py -q` passes"],
    },
    "12b-phase-tests-market-paper-audit.prompt.md": {
        "label": "Phase 12b", "num": "12b",
        "title": "Tests — market data, paper trading, audit",
        "prerequisites": ["Phase 12a complete"],
        "task": "Create tests for ingestion, simulation, paper trading, square-off, trade tax, and audit system.",
        "verification": ["`cd backend && .venv\\Scripts\\python -m pytest tests/integration/test_ingestion.py tests/integration/test_audit.py -q` passes"],
    },
    "12c-phase-tests-recommendations-brackets.prompt.md": {
        "label": "Phase 12c", "num": "12c",
        "title": "Tests — recommendations, midday, trade plans",
        "prerequisites": ["Phase 12b complete"],
        "task": "Create tests for recommendation engine, budget allocator, midday analysis, trade plans, EOD analysis.",
        "verification": ["`cd backend && .venv\\Scripts\\python -m pytest tests/integration/test_recommendation_engine.py -q` passes (may take 1–2 min)"],
    },
    "12d-phase-tests-ui-display-contracts.prompt.md": {
        "label": "Phase 12d", "num": "12d",
        "title": "Tests — UI contracts, lazy loading, displays",
        "prerequisites": ["Phase 12c complete"],
        "task": "Create UI contract tests: trading page, lazy loading, positions/orders displays, charts, live poller.",
        "verification": ["`cd backend && .venv\\Scripts\\python -m pytest tests -q` — expect 455 passed"],
    },
}


FEATURE_COVERAGE = """\
# Feature & Behavior Coverage Audit

This document maps **every major feature and behavior** to the regeneration prompt phase
that embeds its source code, plus manual verification steps.

**Audit date:** generated by `scripts/build_cursor_prompts.py`

---

## Coverage summary

| Area | Phases | Source embedded? | Behavior documented? |
|------|--------|------------------|---------------------|
| Root setup & Windows launchers | 01 | Yes | Yes |
| DB schema (Alembic 001–007) | 02 | Yes | Yes |
| ORM models & schemas | 03 | Yes | Yes |
| NSE/yfinance/Sharekhan providers | 04 | Yes | Yes |
| Paper trading & brackets | 05, 10a | Yes | Yes |
| 79 patterns & backtest | 06, 10b | Yes | Yes |
| Recommendation engine | 07, 10b | Yes | Yes |
| Mid-day analysis | 07, 10c | Yes | Yes |
| FastAPI + audit | 08 | Yes | Yes |
| Streamlit UI modules | 09 | Yes | Yes |
| dashboard.py (7 pages) | 10a–10c | Yes (split) | Yes |
| JSON data files | 11 | Yes | Yes |
| Pytest (455 tests) | 12a–12d | Yes (split) | Yes |
| Architecture docs | 13 | Yes | Yes |
| CI pipelines | 14 | Yes (optional) | Yes |

**Not included (non-essential for Windows rebuild):**
- `scripts/lab/*` — isolated lab/dev environment tooling
- `docs/lab/*` — lab architecture notes
- `.cursor/hooks/*` — Cursor IDE hooks (Mac-specific dev convenience)

---

## 1. Trading page (Phase 10a)

| Feature / behavior | Verification |
|--------------------|--------------|
| Portfolio summary (budget, cash, equity, unrealized/gross realized P&L) | Open Trading tab |
| Data view radio: Positions / Orders / Trades / NIFTY250 (lazy — only active view queries DB) | Switch radio; no lag from inactive views |
| Footer radio: Summary only / Market data & simulation | Select footer section |
| Live polling toggle 10s on Positions only | Enable during market hours |
| Fetch live prices (one-shot) | Positions tab button |
| Reconcile brackets (manual, not on page load) | Positions → Reconcile brackets |
| 3:25 PM IST square-off for brackets + manual positions | `trade_plans.py` + live poller |
| Sidebar: Symbol dropdown, Days slider, Show chart dialog | Sidebar controls |
| Sidebar: Place order (manual); SELL bracket-symbol guard | Place order form |
| Refresh market data → `_market_sync_requested` flag | No blank page; progress fragment |
| `_market_sync_progress_fragment()` — progress only, no body | Phase 10a notes |
| Position intraday chart dialog with target/stop markers | Positions → Chart |
| Symbol history chart dialog from sidebar | Show chart |
| NIFTY250 table: Recommended + Profitable day columns | NIFTY250 radio view |
| Orders tab: current IST session date only; duplicate bracket cleanup | Orders radio view |
| Trades tab: current IST session date only | Trades radio view |
| Position source badges: Rec (blue) vs Manual (orange) | Positions Source column |
| Color legend: current price near target/stop; unrealized P&L green/red | Positions table |
| `_ensure_recommendation_session_state()` auto-load for NIFTY250 | Trading tab with cached recs |
| Stale prior-day session warning (`rec_stale_session`) | After calendar roll-forward |
| Auto-sync scheduled 3:45 PM and 6:00 PM IST | Phase 10c `_scheduled_market_sync_tick` |

---

## 2. Paper trading trend (Phase 10c + 05 + 09)

| Feature / behavior | Verification |
|--------------------|--------------|
| Sharekhan vs Zerodha after-tax comparison (not on Trading tab) | Paper trading trend page |
| Total value at cost and with unrealized P&L | After-tax section |
| Daily P&L trend charts (Plotly) | Auto-load on tab visit |
| Daily results table — typed DateColumn (chronological sort) | Sort by Date column |
| Closed trades ledger (last 30 days, Rec/Manual source) | Closed trades section |
| Pattern performance breakdown | Pattern breakdown section |
| Refresh trend button | Manual reload |

---

## 3. Pattern backtest (Phase 10b + 06 + 09)

| Feature / behavior | Verification |
|--------------------|--------------|
| Universe selector (NIFTY250 default) | Backtest page |
| Section radio: Today's validation / 30-day simulation (lazy) | Switch sections |
| 30-day simulation from cache or hard refresh | Simulation section |
| Today's validation scorecard | Validation section |
| Pattern leaderboard with drill-down | Leaderboard |
| `SIM_BACKTEST` background job with progress | Hard refresh |
| `TODAY_PREDICTION` background job | Run prediction |

---

## 4. Recommendations (Phase 10b + 07 + 09 + 11)

| Feature / behavior | Verification |
|--------------------|--------------|
| Auto-load cached DB snapshot on tab visit | Open Recommendations |
| Daily budget input (`DAILY_TRADING_BUDGET_INR`) | Budget field |
| Run analysis → `RECOMMENDATIONS` job | No blank page; `_rec_live_poll` |
| Section radio: Stock picks / Budget & orders / Budget simulation | Lazy sections |
| Stock picks: one tier/bucket at a time via tier radio | Tier radio in picks section |
| Cap tiers: large ≥₹100, mid ≥₹30, small ≥₹10 | Tier tables |
| Price buckets (non-overlapping with cap tiers) | Bucket tables |
| Place trade / Place order for all (valid brackets only) | Budget & orders section |
| Already-placed lines: disabled "Order placed" | After placing |
| Invalid brackets: disabled "Invalid bracket" | Bracket validation |
| Budget simulation read-only (`rec_sim_budget` separate key) | Budget simulation section |
| 15-day pattern ranking window | `recommendation_engine.py` |
| Min expected move ₹1; min relative volume 0.75× | `recommendation_universe.json` |
| NR4 confluence boost; pattern exclusions; per-pattern pick cap | Engine + JSON config |
| Net-profit gate (₹5,500 reference, ₹1 min net after tax) | Engine |
| Snapshot saved to `recommendation_snapshots` table | After Run analysis |
| Prediction date: before 4:30 PM → today; after → next trading day | `market_calendar.py` |

---

## 5. Mid-day recommendation analysis (Phase 10c + 07 + 09)

| Feature / behavior | Verification |
|--------------------|--------------|
| Run button: trading days 11:45 AM–4:30 PM IST only | Mid-day page |
| Read-only budget (morning budget − open cost − \\|realized P&L\\|) | Budget metrics |
| Morning DB snapshot prerequisite | Warning if missing |
| `MIDDAY_RECOMMENDATIONS` job: session OHLC sync (~250 symbols) then engine | Progress messages |
| Daily JSON cache (`midday_recommendation_snapshot.json`) | Auto-load on tab |
| Comparison table: mid-day vs morning deltas | Analysis section |
| Section radio: Analysis / Place orders (lazy) | Switch sections |
| Place order calibrations: NEW / PENDING_CALIBRATE / OPEN_CALIBRATE | Place orders section |
| Does not overwrite morning DB snapshot | Separate cache file |

---

## 6. Analysis & EOD (Phase 10c + 05 + 09)

| Feature / behavior | Verification |
|--------------------|--------------|
| Trade date selector | EOD page |
| Manual Refresh EOD (not auto on first visit) | Refresh button |
| Win rate, target hits, stop hits, avg P&L metrics | Metrics section |
| Per-trade breakdown table | Breakdown |
| Missed targets section | Missed targets |
| NIFTY250 profitable closes not recommended (after 3:45 PM IST) | Orange rows section |
| Alternative patterns that would have worked | Alternatives |
| Insights for tomorrow | Insights section |

---

## 7. Pattern definitions (Phase 09 + 06 + 11)

| Feature / behavior | Verification |
|--------------------|--------------|
| Catalog of all 79 registered patterns | Pattern definitions page |
| Pattern descriptions | Catalog table |
| Synthetic example charts | Example chart per pattern |

---

## 8. Paper trading engine (Phase 05)

| Feature / behavior | Verification |
|--------------------|--------------|
| MARKET and LIMIT order types | Manual place order |
| Order statuses: PENDING, FILLED, CANCELLED, REJECTED | Orders tab |
| Weighted avg cost on BUY | Positions |
| Unrealized P&L with live LTP or EOD mark | Positions |
| Realized P&L on SELL | Trades tab |
| Cannot sell more than held | SELL validation |
| Budget enforcement via `budget_portfolio.py` | BUY validation |
| Bracket: entry limit → target + stop child orders | Trade plans |
| `PaperTradePlan` statuses: PENDING_ENTRY, OPEN, CLOSED, etc. | Trade plans |
| 3:25 PM IST time exit (migration 007) | `trade_plans.py` |
| Offline bracket catch-up after downtime | Reconcile brackets |
| Retention window 30 days (`PAPER_TRADING_RETENTION_DAYS`) | Paper trend |
| Indian delivery tax: STCG, STT, stamp, brokerage, NSE, SEBI, GST | `trade_tax.py` |

---

## 9. Market data & providers (Phase 04)

| Feature / behavior | Verification |
|--------------------|--------------|
| `DATA_PROVIDER=nse` default | `.env` |
| NSE EOD candles + live quotes | Refresh market data |
| yfinance fallback for index | Provider code |
| Sharekhan stub (Phase 2) | `sharekhan_provider.py` |
| NIFTY250 universe sync | Market sync job |
| NSE trading holidays JSON | Calendar skips weekends/holidays |
| Backfill 120 days default (`BACKFILL_DAYS`) | Bootstrap / sync |
| Candle quality checks | `candle_quality.py` |
| Applicable rates refresh (STCG, STT) | Scheduled + manual |

---

## 10. Background jobs (Phase 09 + 10a–10c)

| Job kind | Trigger | Phase |
|----------|---------|-------|
| `MARKET_SYNC` | Refresh market data | 10a |
| `SIM_BACKTEST` | Hard refresh backtest | 10b |
| `TODAY_PREDICTION` | Run prediction | 10b |
| `RECOMMENDATIONS` | Run analysis | 10b |
| `MIDDAY_RECOMMENDATIONS` | Run mid-day analysis | 10c |

| Behavior | Verification |
|----------|--------------|
| Only one job at a time (others disabled) | UI buttons while job running |
| Jobs survive tab switches (session_id keyed registry) | Switch tabs during job |
| Progress via `@st.fragment(run_every=1s)` | Sidebar + inline progress |
| Failed jobs → FAILED audit; no auto-retry | Audit logs |
| Never `st.rerun()` immediately after job start | No blank page |

---

## 11. Async DB access (Phase 09)

| Behavior | Verification |
|----------|--------------|
| Single background asyncio loop in daemon thread | `async_runner.py` |
| All DB via `run_async()` | No `asyncio.run()` in UI |
| Exclusive `asyncio.Lock` for asyncpg | No "another operation in progress" |
| Pool dispose on loop restart / InterfaceError | Error recovery |

---

## 12. Audit & observability (Phase 08)

| Behavior | Verification |
|----------|--------------|
| Audit to PostgreSQL + logging (composite backend) | `AUDIT_BACKEND=composite` |
| API request logging | FastAPI middleware |
| Tab switch timing audit (`ui.tab_switch`) | `tab_switch_audit.py` |
| Page render timing (`ui.page_render`) | All pages |
| Job audit events | Background jobs |

---

## 13. FastAPI REST (Phase 08) — optional

| Endpoint | Method |
|----------|--------|
| `/api/instruments` | GET |
| `/api/instruments/{symbol}/candles` | GET |
| `/api/market/summary` | GET |
| `/api/paper/account`, `/positions`, `/orders`, `/trades` | GET |
| `/api/paper/orders` | POST |
| `/api/paper/orders/{id}` | DELETE |
| `/api/admin/sync` | POST |
| `/docs` | Swagger UI |

---

## 14. Tests (Phases 12a–12d)

| Group | Files | ~Tests |
|-------|-------|--------|
| 12a Core/config/smoke | 15 | ~80 |
| 12b Market/paper/audit | 20 | ~150 |
| 12c Recommendations/brackets | 10 | ~120 |
| 12d UI contracts/displays | 10 | ~105 |
| **Total** | **55** | **455** |

Key contract tests:
- `test_trading_page_ui_contract.py` — Trading page structure
- `test_lazy_loading.py` — section radios defer DB work
- `test_dashboard_import_contract.py` — hot-reload imports
- `test_background_jobs.py` — job lifecycle
- `test_recommendation_engine.py` — full engine logic

---

## 15. Session state keys (reference)

| Key | Page | Purpose |
|-----|------|---------|
| `nav_page` | All | Sidebar navigation |
| `trading_data_tab` | Trading | Positions/Orders/Trades/NIFTY250 |
| `trading_footer_section` | Trading | Summary / Market data footer |
| `rec_page_section` | Recommendations | Picks / Budget / Simulation |
| `rec_tier_view` | Recommendations | Selected tier or bucket |
| `midday_page_section` | Mid-day | Analysis / Place orders |
| `backtest_page_section` | Backtest | Validation / Simulation |
| `live_polling_enabled` | Trading | Live quote toggle |
| `_market_sync_requested` | Trading | Market sync job trigger |
| `_rec_live_poll` | Recommendations | Rec job poll flag |
| `_midday_live_poll` | Mid-day | Mid-day job poll flag |
| `position_live_quotes` | Trading | LTP cache per symbol |
| `rec_report` / `rec_allocation` | Rec/Trading | Cached recommendation session |
| `midday_report` / `midday_allocation` | Mid-day | Mid-day session |
| `rec_sim_budget` | Recommendations | Budget simulation (isolated) |

---

## Gaps & optional additions

| Item | Status | Notes |
|------|--------|-------|
| Lab scripts (`scripts/lab/*`) | Not embedded | Dev isolation tooling; not needed for production rebuild |
| Cursor hooks (`.cursor/hooks/*`) | Not embedded | IDE convenience only |
| Sharekhan live integration | Stub only | Phase 2; provider stub in Phase 04 |
| Docker database scripts | In Phase 01 | `recover_docker_database.sh` included |

---

*Use [99-verification.prompt.md](99-verification.prompt.md) after all phases for final smoke test.*
"""


README = """\
# Cursor Regeneration Prompts — NIFTY Paper Trading Platform

**Purpose:** Recreate this entire project on a Windows laptop using Cursor Agent **without**
copying source code, Git history, zip archives, or PDFs from the original Mac.

Each `.prompt.md` file is a **self-contained Cursor prompt** with **full file contents
embedded inline**. You only need plain text — no binary transfers.

See **[FEATURE-COVERAGE.md](FEATURE-COVERAGE.md)** for a detailed audit of every feature,
behavior, and which phase covers it.

---

## How to transfer these prompts to Windows

| Method | Steps |
|--------|-------|
| **Email / cloud note** | Copy each `.prompt.md` text into Gmail/Drive/OneNote |
| **USB text only** | Save prompts as `.txt` on USB — text files only |
| **Chunked sessions** | Large phases are pre-split (10a–c, 12a–d) for ~25–80 KB chunks |

---

## Execution order

Run phases **in order**. Do not skip.

| Step | File | What it creates | ~Size |
|------|------|-----------------|-------|
| **Start** | [00-MASTER-PROMPT.md](00-MASTER-PROMPT.md) | Project overview + critical patterns | 8 KB |
| | [FEATURE-COVERAGE.md](FEATURE-COVERAGE.md) | Feature/behavior audit (reference) | 12 KB |
| 01 | [01-phase-root-setup.prompt.md](01-phase-root-setup.prompt.md) | Setup.py, run_app.py, .bat files | 62 KB |
| 02 | [02-phase-backend-config-db.prompt.md](02-phase-backend-config-db.prompt.md) | pyproject, Alembic 001–007 | 36 KB |
| 03 | [03-phase-models-schemas.prompt.md](03-phase-models-schemas.prompt.md) | Models, schemas | 19 KB |
| 04 | [04-phase-providers-ingestion.prompt.md](04-phase-providers-ingestion.prompt.md) | Providers, ingestion | 102 KB |
| 05 | [05-phase-paper-trading.prompt.md](05-phase-paper-trading.prompt.md) | Paper trading, brackets | 142 KB |
| 06 | [06-phase-patterns-backtest.prompt.md](06-phase-patterns-backtest.prompt.md) | 79 patterns, backtest | 112 KB |
| 07 | [07-phase-recommendations.prompt.md](07-phase-recommendations.prompt.md) | Recommendation engine | 91 KB |
| 08 | [08-phase-api-audit.prompt.md](08-phase-api-audit.prompt.md) | FastAPI, audit | 46 KB |
| 09 | [09-phase-streamlit-ui-core.prompt.md](09-phase-streamlit-ui-core.prompt.md) | UI modules (not dashboard) | 190 KB |
| 10a | [10a-phase-dashboard-part1-trading.prompt.md](10a-phase-dashboard-part1-trading.prompt.md) | dashboard.py lines 1–1217 | ~40 KB |
| 10b | [10b-phase-dashboard-part2-backtest-recs.prompt.md](10b-phase-dashboard-part2-backtest-recs.prompt.md) | dashboard.py lines 1218–2388 | ~38 KB |
| 10c | [10c-phase-dashboard-part3-midday-eod-main.prompt.md](10c-phase-dashboard-part3-midday-eod-main.prompt.md) | dashboard.py lines 2389–3062 | ~22 KB |
| 11 | [11-phase-data-json.prompt.md](11-phase-data-json.prompt.md) | JSON data files | 63 KB |
| 12a | [12a-phase-tests-core-config.prompt.md](12a-phase-tests-core-config.prompt.md) | Tests: core/smoke | ~45 KB |
| 12b | [12b-phase-tests-market-paper-audit.prompt.md](12b-phase-tests-market-paper-audit.prompt.md) | Tests: market/paper/audit | ~95 KB |
| 12c | [12c-phase-tests-recommendations-brackets.prompt.md](12c-phase-tests-recommendations-brackets.prompt.md) | Tests: recs/brackets | ~110 KB |
| 12d | [12d-phase-tests-ui-display-contracts.prompt.md](12d-phase-tests-ui-display-contracts.prompt.md) | Tests: UI contracts | ~52 KB |
| 13 | [13-phase-architecture-docs.prompt.md](13-phase-architecture-docs.prompt.md) | Architecture docs (optional) | 162 KB |
| 14 | [14-phase-ci-optional.prompt.md](14-phase-ci-optional.prompt.md) | CI pipelines (optional) | small |
| **End** | [99-verification.prompt.md](99-verification.prompt.md) | Final smoke test | 3 KB |

---

## Windows prerequisites

1. **Python 3.11+** — check "Add to PATH"
2. **PostgreSQL 15+**:
   ```sql
   CREATE USER trading WITH PASSWORD 'trading';
   CREATE DATABASE trading OWNER trading;
   ```
3. **Cursor** — open empty folder as workspace

---

## After all phases

```bat
cd C:\\Users\\<you>\\projects\\trading
python Setup.py
copy backend\\env.example backend\\.env
python scripts\\run_app.py
```

Open **http://localhost:8501** — run **99-verification.prompt.md** checklist.

---

## Regenerating (on Mac only)

```bash
python scripts/build_cursor_prompts.py
```

*Generated for offline Windows rebuild — no PDFs, no zip, no Git required.*
"""


MASTER_PROMPT = """\
# Master Cursor Prompt — NIFTY Paper Trading Simulation Platform

> **Read this first.** Then execute phases 01 → 14 in order. Large phases are split:
> dashboard = 10a+10b+10c, tests = 12a+12b+12c+12d.
> See **FEATURE-COVERAGE.md** for the full behavior checklist.

---

## Cursor Agent Prompt (copy from here)

You are rebuilding the **NIFTY Paper Trading Simulation Platform** on Windows from text
specifications only. No source code, Git, zip, or PDFs available.

### Stack

Python 3.11+ · PostgreSQL 15+ (asyncpg) · Streamlit :8501 · FastAPI :8000 (optional) · Alembic 001–007 · pytest (455 tests)

### Seven Streamlit pages

1. **Trading** — portfolio, lazy radio views, live polling 10s, market sync, bracket orders
2. **Paper trading trend** — Sharekhan vs Zerodha after-tax, daily P&L charts
3. **Pattern backtest** — 30-day simulation + today's validation, 79 patterns
4. **Recommendations** — budget, run analysis, tier/bucket picks, place orders, budget simulation
5. **Mid day recommendation analysis** — 11:45–4:30 PM IST run, comparison vs morning
6. **Analysis & EOD** — trade analysis, missed profitable NIFTY250 after 3:45 PM
7. **Pattern definitions** — catalog + example charts

### Critical patterns (DO NOT deviate)

1. **`ui/async_runner.py`**: ONE background loop; all DB via `run_async()`; exclusive lock
2. **`ui/background_jobs.py`**: 5 JobKinds; fragment polling; **never** `st.rerun()` after job start
3. **Market sync blank-page fix**: `_market_sync_requested` + progress-only fragment (no body in fragment)
4. **Rec/mid-day blank-page fix**: `_rec_live_poll` / `_midday_live_poll` + live fragments
5. **Lazy loading**: `st.radio` sections — not `st.tabs` (tabs run all bodies every rerun)
6. **Live polling**: 10s fragment on Positions only; background thread; 3:25 PM square-off
7. **Brackets**: manual reconcile only; state in `bracket_reconcile_state.json`
8. **Hot reload**: `streamlit_imports.ensure_fresh_ui_modules()` on each dashboard load

### dashboard.py assembly (Phases 10a → 10b → 10c)

| Part | Lines | Content |
|------|-------|---------|
| 10a | 1–1217 | Trading page, live polling, market sync |
| 10b | 1218–2388 | Backtest, Recommendations |
| 10c | 2389–3062 | Mid-day, EOD, Paper trend, main() |

10a **creates** the file; 10b and 10c **append**. Final file = 3062 lines.

### Environment defaults

```env
DATABASE_URL=postgresql+asyncpg://trading:trading@localhost:5432/trading
PAPER_STARTING_CASH=1000000
DATA_PROVIDER=nse
BACKFILL_DAYS=120
MARKET_DATA_UNIVERSE=NIFTY250
DAILY_TRADING_BUDGET_INR=1000000
AUDIT_ENABLED=true
AUDIT_BACKEND=composite
```

### Execution workflow

1. Phase 01 → 09: standard file creation
2. Phase 10a → 10c: assemble dashboard.py
3. Phase 11: JSON data files
4. Phase 12a → 12d: test suite (455 tests)
5. Phase 13: architecture docs (optional)
6. Phase 14: CI configs (optional)
7. Run Setup.py, copy .env, `python scripts/run_app.py`
8. **99-verification.prompt.md** final checklist

### Rules

- Copy code blocks **verbatim** — no summarization
- Exact file paths relative to `trading/` workspace root
- Verify each phase before proceeding
- Consult FEATURE-COVERAGE.md if unsure whether a behavior is covered

**Start with Phase 01:** `01-phase-root-setup.prompt.md`

*End of master prompt.*
"""


VERIFICATION = """\
# Final Verification Prompt — NIFTY Paper Trading Platform

> Run after Phases 01–12d (and optionally 13–14). See FEATURE-COVERAGE.md for full behavior checklist.

---

## Cursor Agent Prompt (copy from here)

### 1. Setup

```bat
cd C:\\Users\\<you>\\projects\\trading
python Setup.py
copy backend\\env.example backend\\.env
cd backend
.venv\\Scripts\\alembic upgrade head
.venv\\Scripts\\python -m app.bootstrap
cd ..
python scripts\\run_app.py
```

### 2. File sanity

- [ ] `backend/ui/dashboard.py` is **3062 lines**
- [ ] `backend/alembic/versions/` has **7** migration files
- [ ] `backend/app/data/` has **9** JSON files
- [ ] `backend/tests/` has **55** test files
- [ ] `strategies/registry.py` registers **≥79** patterns

### 3. All 7 pages load

- [ ] Trading · Paper trading trend · Pattern backtest · Recommendations
- [ ] Mid day recommendation analysis · Analysis & EOD · Pattern definitions

### 4. Critical behaviors (no blank page)

- [ ] Refresh market data → progress bar, page stays visible
- [ ] Run recommendation analysis → job progress, no blank page
- [ ] Run mid-day analysis → job progress, no blank page
- [ ] Trading radio views lazy-load (switch Positions/Orders/Trades/NIFTY250)
- [ ] Recommendations section radio lazy-loads (Picks/Budget/Simulation)

### 5. Trading tab specifics

- [ ] Live polling toggle on Positions (10s during market hours)
- [ ] Reconcile brackets button (manual, not auto on load)
- [ ] Show chart dialog (sidebar) dismisses cleanly
- [ ] Position Chart dialog opens from Positions tab
- [ ] NIFTY250 view shows Recommended column

### 6. Recommendations specifics

- [ ] Auto-loads cached snapshot on tab open
- [ ] Budget simulation section is read-only (no place orders)
- [ ] Place order disabled for already-placed lines

### 7. Mid-day specifics

- [ ] Budget is read-only (computed from morning snapshot)
- [ ] Analysis / Place orders section radio works

### 8. EOD specifics

- [ ] Manual Refresh EOD (not auto-built on first visit)
- [ ] Date picker works

### 9. Paper trading trend

- [ ] Sharekhan vs Zerodha after-tax comparison visible
- [ ] Daily results Date column sorts chronologically

### 10. Tests

```bat
cd backend
.venv\\Scripts\\python -m pytest tests -q
```

- [ ] **455 tests passed** (some may skip without live NSE)

### 11. Optional API

```bat
.venv\\Scripts\\uvicorn app.main:app --port 8000
```

- [ ] http://localhost:8000/docs loads

---

*End of verification prompt.*
"""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Remove legacy unsplit files if present
    for legacy in ("10-phase-streamlit-dashboard.prompt.md", "12-phase-tests.prompt.md"):
        legacy_path = OUT / legacy
        if legacy_path.exists():
            legacy_path.unlink()

    readme_text = README.replace(
        "*Generated for offline Windows rebuild",
        f"*Generated: {generated}*\n\n*Generated for offline Windows rebuild",
    )
    (OUT / "README.md").write_text(readme_text, encoding="utf-8")
    (OUT / "00-MASTER-PROMPT.md").write_text(MASTER_PROMPT, encoding="utf-8")
    (OUT / "99-verification.prompt.md").write_text(VERIFICATION, encoding="utf-8")
    (OUT / "FEATURE-COVERAGE.md").write_text(FEATURE_COVERAGE.replace(
        "generated by `scripts/build_cursor_prompts.py`",
        f"generated {generated}",
    ), encoding="utf-8")

    manifest: list[str] = []
    total_files = 0
    total_bytes = 0

    for phase in STANDARD_PHASES:
        missing = [p for p in phase["paths"] if not (ROOT / p).exists()]
        if missing:
            raise FileNotFoundError(f"{phase['file']} missing: {missing}")
        _, nfiles, size = write_files_phase(phase)
        total_files += nfiles
        total_bytes += size
        manifest.append(f"| {phase['label']} | `{phase['file']}` | {nfiles} files | {size / 1024:.0f} KB |")

    for split in DASHBOARD_SPLITS:
        rel = split["dashboard_path"]
        start, end = split["line_range"]
        total_lines = len(read_file(rel).splitlines())
        if end > total_lines:
            raise ValueError(f"{split['file']}: line range {start}-{end} exceeds file ({total_lines} lines)")
        _, _, size = write_dashboard_split_phase(split)
        total_files += 1
        total_bytes += size
        manifest.append(
            f"| {split['label']} | `{split['file']}` | dashboard L{start}–{end} | {size / 1024:.0f} KB |"
        )

    for filename, paths in TEST_GROUPS.items():
        meta = TEST_PHASE_META[filename]
        missing = [p for p in paths if not (ROOT / p).exists()]
        if missing:
            raise FileNotFoundError(f"{filename} missing: {missing}")
        phase = {
            "file": filename,
            "label": meta["label"],
            "title": meta["title"],
            "prerequisites": meta["prerequisites"],
            "task": meta["task"],
            "paths": paths,
            "verification": meta["verification"],
        }
        _, nfiles, size = write_files_phase(phase)
        total_files += nfiles
        total_bytes += size
        manifest.append(f"| {meta['label']} | `{filename}` | {nfiles} files | {size / 1024:.0f} KB |")

    manifest_text = "\n".join(manifest)
    index = f"""\
# Prompt Index

Generated: {generated}

| Phase | File | Contents | Size |
|-------|------|----------|------|
{manifest_text}
| — | **Total** | **{total_files} source units** | **{total_bytes / 1024 / 1024:.1f} MB** |

Also see: [FEATURE-COVERAGE.md](FEATURE-COVERAGE.md) — full behavior audit.

Split phases: **10a–10c** (dashboard.py), **12a–12d** (tests).
"""
    (OUT / "INDEX.md").write_text(index, encoding="utf-8")

    n_prompts = len(STANDARD_PHASES) + len(DASHBOARD_SPLITS) + len(TEST_GROUPS) + 4
    print(f"Generated {n_prompts} prompt files in {OUT}")
    print(f"Embedded {total_files} source units ({total_bytes / 1024 / 1024:.1f} MB)")
    print("Split: dashboard 10a-10c, tests 12a-12d")
    print("Added: FEATURE-COVERAGE.md")


if __name__ == "__main__":
    main()

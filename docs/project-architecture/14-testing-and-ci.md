# Testing & CI

Test suite structure, markers, and continuous integration pipelines.

## Test layout

```
backend/tests/
├── conftest.py                 # Shared fixtures, DATABASE_URL, noop audit writer (autouse)
├── integration/                # Domain + UI contract tests
│   ├── test_ingestion.py
│   ├── test_trade_plans.py
│   ├── test_trade_tax.py
│   ├── test_budget_portfolio.py
│   ├── test_allocation_trade_plan_state.py
│   ├── test_async_runner_db.py
│   ├── test_recommendation_engine.py
│   ├── test_recommendation_cache.py
│   ├── test_midday_recommendations.py
│   ├── test_midday_budget.py
│   ├── test_lazy_loading.py
│   ├── test_simulation_cache.py
│   ├── test_eod_trade_analysis.py
│   ├── test_live_quotes.py
│   ├── test_live_quote_poller.py
│   ├── test_positions_display.py
│   ├── test_trading_page_ui_contract.py
│   ├── test_tab_switch_audit.py
│   ├── test_fastapi_smoke.py
│   ├── test_audit.py
│   ├── test_audit_backends.py
│   ├── test_audit_catalog.py
│   ├── test_audit_error_handling.py
│   ├── test_regression_from_audit.py
│   ├── test_budget_allocator.py
│   ├── test_recommendations_display.py
│   ├── test_dashboard_import_contract.py
│   └── ...
└── post_deploy/
    ├── api_catalog.py          # Expected API route catalog
    ├── test_api_smoke.py       # Live API smoke tests
    └── test_ui_smoke.py        # UI module import smoke
```

---

## Running tests

```bash
cd backend

# Full suite (needs PostgreSQL)
./scripts/run_tests.sh all

# Fast checks (no DB)
./scripts/run_tests.sh quick

# Post-deploy smoke (needs Postgres or running API)
./scripts/run_tests.sh post_deploy
```

**From repo root:**

```bash
make test
make test-quick
make test-post-deploy
```

---

## Pytest markers

Defined in `backend/pyproject.toml`:

| Marker | Description | Requires DB |
|--------|-------------|-------------|
| `quick` | Fast import/contract checks | No |
| `db` | Database integration tests | Yes |
| `post_deploy` | Deployment smoke tests | Yes / running server |

**Examples:**

```bash
pytest -m quick                    # fast only
pytest -m "db and not slow"        # DB tests
pytest tests/integration/test_trade_plans.py -v
```

---

## Test categories

### Integration tests

Cover real business logic against PostgreSQL:

| Area | Test file |
|------|-----------|
| Market sync | `test_ingestion.py` |
| Bracket plans | `test_trade_plans.py` (EOD/live fills, NSE catch-up, duplicate session guard, cleanup) |
| Bracket reconcile state | `test_bracket_reconcile_state.py` |
| Chart dialogs | `test_position_intraday_chart.py`, `test_symbol_history_chart.py` |
| Trading UI contract | `test_trading_page_ui_contract.py` (lazy radios, no NIFTY250 snapshot, manual reconcile, on-demand charts) |
| Lazy loading | `test_lazy_loading.py` (`_load_trading_page_data` flags, `_load_midday_place_state`, section helpers) |
| Allocation placed-state | `test_allocation_trade_plan_state.py` |
| Mid-day recommendations | `test_midday_recommendations.py` (`is_midday_action_applied`, comparison rows) |
| Streamlit async DB | `test_async_runner_db.py` (parallel `run_async`, trading page mock render) |
| Recommendations | `test_recommendation_engine.py`, `test_recommendation_cache.py`, `test_recommendations_display.py` |
| Budget allocation | `test_budget_allocator.py` (invalid bracket skip/backfill), `test_budget_portfolio.py` |
| Backtest cache | `test_simulation_cache.py` |
| EOD analysis | `test_eod_trade_analysis.py` (as-of date on selected trade date) |
| Live quotes | `test_live_quotes.py` (poll + NSE day extremes), `test_live_quote_poller.py` |
| NIFTY250 index | `test_nifty250_index.py` |
| Paper trading trend | `test_paper_trading_trend.py` (typed Date column sort) |
| Budget / portfolio | `test_budget_portfolio.py` |
| Trade tax / dual broker | `test_trade_tax.py` |
| Patterns | `test_patterns_registry.py`, `test_pattern_definitions.py` |
| Audit | `test_audit.py`, `test_audit_backends.py`, `test_audit_catalog.py`, `test_audit_error_handling.py` |
| Audit regressions | `test_regression_from_audit.py` |
| Market calendar | `test_market_calendar.py` |
| Background jobs | `test_background_jobs.py` |
| UI contracts | `test_dashboard_import_contract.py`, `test_ui_contract.py` |
| FastAPI | `test_fastapi_smoke.py` |

### Post-deploy tests

Validate a running or freshly migrated deployment:

```bash
# Against local API
export POST_DEPLOY_API_URL=http://localhost:8000
./scripts/run_tests.sh post_deploy

# Optional mutating tests (backtest run, admin sync)
POST_DEPLOY_RUN_MUTATING=1 ./scripts/run_tests.sh post_deploy
```

**API catalog:** `tests/post_deploy/api_catalog.py` — every GET route must return non-5xx.

---

## CI pipelines

```mermaid
flowchart LR
    subgraph gitlab [GitLab CI]
        G1[integration-tests]
        G2[post-deploy-tests]
    end

    subgraph github [GitHub Actions]
        H1[integration-tests]
        H2[post-deploy-tests]
    end

    G1 --> PG[(PostgreSQL 15 service)]
    G2 --> PG
    H1 --> PG
    H2 --> PG
```

| Platform | File | Trigger |
|----------|------|---------|
| GitLab | `.gitlab-ci.yml` | MR, main, tags |
| GitHub | `.github/workflows/integration-tests.yml` | push/PR to main/master |

Both pipelines:

1. **integration-tests** — Python 3.11, install deps, run full pytest suite
2. **post-deploy-tests** — Same + PostgreSQL 15 service container

---

## Local development hooks

**Cursor hooks:** `.cursor/hooks.json`

- Runs quick tests after Python edits under `backend/app` and `backend/ui`
- Optional full suite on turn complete

**Script:** `.cursor/hooks/run-integration-tests.sh`

---

## Test database

Tests use `DATABASE_URL` from environment or conftest default:

```
postgresql+asyncpg://trading:trading@localhost:5432/trading
```

Under pytest, the FastAPI engine (`app.db.session`) uses `NullPool` so asyncpg connections are not shared across pytest-asyncio and TestClient/anyio event loops.

**Audit isolation:** `tests/conftest.py` sets `NoOpAuditWriter` autouse so integration tests never persist audit rows to PostgreSQL. Override with the `audit_writer` fixture when testing audit persistence behavior.

Tests may mutate data — use a dedicated test database in shared environments:

```env
DATABASE_URL=postgresql+asyncpg://trading:trading@localhost:5432/trading_test
```

---

## Writing new tests

1. Add file under `tests/integration/`
2. Use `@pytest.mark.db` if PostgreSQL required
3. Use `@pytest.mark.quick` for fast import-only tests
4. Follow existing async patterns from `conftest.py`
5. Run `./scripts/run_tests.sh all` before merging

**Fixture pattern:**

```python
@pytest.mark.db
async def test_something(async_session):
    # use async_session for DB operations
    ...
```

---

## Expected test counts

Last known full suite: **322 passed** (`-m quick`), 45 deselected; post-deploy mutating tests skipped by default.

**Audit tests** (`test_audit*.py`, `test_regression_from_audit.py`, `test_trading_page_ui_contract.py`, `test_positions_display.py`) cover middleware, global exception hooks, fire-and-forget dispatch, Postgres writer field mapping, production action catalog, correlation-id chaining, background-job failure paths, stale Streamlit module contracts, Trading tab UI contracts (removed sections, Positions columns, live quote cache), and dual-broker portfolio summary — without requiring Sentry or external log aggregation.

Next: [Operations runbook](15-operations-runbook.md)

# Configuration

Environment variables, defaults, and static data files.

## Configuration sources

```mermaid
flowchart LR
    ENV[backend/.env] --> CFG[app/config.py Settings]
    DEF[app/defaults.py] --> CFG
    CFG --> SVC[Services & Providers]
    DATA[app/data/*.json] --> SVC
```

**Priority:** Environment variables override `Settings` defaults. `.env` is loaded automatically by Pydantic Settings.

**Template:** Copy `backend/env.example` → `backend/.env`

---

## Environment variables

| Variable | Default (code) | env.example | Description |
|----------|----------------|-------------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://trading:trading@localhost:5432/trading` | same | Async PostgreSQL connection — **do not** append `?options=` (asyncpg rejects it) |
| `PAPER_STARTING_CASH` | `50000` | `1000000` | Initial paper account cash (INR) |
| `DATA_PROVIDER` | `nse` | `nse` | Market data source: `nse`, `yfinance`, `sharekhan` |
| `BACKFILL_DAYS` | `120` | `120` | Days of OHLCV history to retain/fetch |
| `MARKET_DATA_UNIVERSE` | `NIFTY250` | `NIFTY250` | Primary symbols synced to DB (see also recommendation cap-tier list) |
| `DEFAULT_SIMULATION_UNIVERSE` | `NIFTY250` | `NIFTY250` | Default backtest universe |
| `MAX_TARGET_PROFIT_PCT` | `80.0` | `80` | Cap on model sell target % |
| `PAPER_TRADING_RETENTION_DAYS` | `30` | `30` | Keep paper orders/trades/plans; trend tab window |
| `DAILY_TRADING_BUDGET_INR` | `50000` | `1000000` | Daily budget for recommendations |
| `AUDIT_ENABLED` | `true` | `true` | Master audit switch |
| `AUDIT_LOG_API_REQUESTS` | `true` | `true` | Log each HTTP request |
| `AUDIT_BACKEND` | `composite` | `composite` | `postgres`, `logging`, `composite`, `noop` |
| `AUDIT_BLOCKING` | `false` | `false` | Wait for DB write (debug/tests only; default is fire-and-forget) |
| `AUDIT_CAPTURE_LOG_ERRORS` | `true` | `true` | Persist ERROR stdlib logs to DB |
| `AUDIT_CAPTURE_UNHANDLED_EXCEPTIONS` | `true` | `true` | Persist uncaught exceptions to DB |
| `AUDIT_TRACEBACK_MAX_CHARS` | `4000` | `4000` | Truncate stack traces in audit |
| `SHAREKHAN_API_KEY` | — | — | Sharekhan credentials (Phase 2) |
| `SHAREKHAN_CUSTOMER_ID` | — | — | |
| `SHAREKHAN_ACCESS_TOKEN` | — | — | |

### Code-only defaults (`app/defaults.py`)

| Constant | Value | Notes |
|----------|-------|-------|
| `DEFAULT_DAILY_TRADING_BUDGET_INR` | `50000` | Used when env not set |
| `DEFAULT_MAX_TARGET_PROFIT_PCT` | `80.0` | Used when env not set |
| `DEFAULT_PAPER_TRADING_RETENTION_DAYS` | `30` | Trend window + DB prune age |
| `DEFAULT_MARKET_DATA_UNIVERSE` | `NIFTY250` | |
| `DEFAULT_SIMULATION_UNIVERSE` | `NIFTY250` | |
| `DEFAULT_STCG_TAX_RATE` | `0.20` | Fallback STCG (20%) |
| `DEFAULT_STT_RATE` | `0.001` | Fallback STT (0.1% per leg) |
| `DEFAULT_STAMP_DUTY_RATE` | `0.00015` | Fallback stamp duty |
| `DEFAULT_BROKERAGE_RATE` | `0.003` | Sharekhan-aligned fallback (0.30%) |
| `DEFAULT_BROKERAGE_MIN_PER_SHARE_INR` | `0.01` | Min brokerage per share |
| `DEFAULT_EXCHANGE_TXN_RATE` | `0.0000297` | NSE txn charge fallback |
| `DEFAULT_SEBI_TURNOVER_RATE` | `1e-7` | SEBI turnover fee |
| `DEFAULT_GST_RATE` | `0.18` | GST on brokerage + exchange + SEBI |
| `DEFAULT_BROKER_PROFILE` | `sharekhan_delivery` | Default profile id for recommendations |
| `DEFAULT_DP_CHARGE_PER_SCRIP_INR` | `0.0` | Sharekhan DP (nil via broker) |
| `DEFAULT_ZERODHA_DP_CHARGE_PER_SCRIP_INR` | `15.34` | Zerodha DP debit per scrip on sell |
| `DEFAULT_CONSERVATIVE_EXIT_RATIO` | `0.5` | Actual sell = half of model upside |

### Code-only settings (`app/config.py`)

| Setting | Default | Description |
|---------|---------|-------------|
| `conservative_exit_ratio` | `0.5` | Exit ratio for conservative targets |
| `stcg_tax_rate` | `0.20` | 20% STCG |
| `stt_rate` | `0.001` | 0.1% STT |
| `stamp_duty_rate` | `0.00015` | Stamp duty |
| `brokerage_rate` | `0.0003` | Brokerage |

> **Note:** `env.example` uses ₹10,00,000 for cash/budget; code defaults use ₹50,000. Always set `.env` explicitly for production-like values.

---

## Data provider selection

```mermaid
flowchart TD
    A[DATA_PROVIDER env] --> B{value?}
    B -->|nse| C[NSEProvider]
    B -->|yfinance| D[YFinanceProvider]
    B -->|sharekhan| E[SharekhanProvider]
    C --> F[nsefeed BhavCopy + quote API]
    D --> G[Yahoo Finance]
    E --> H[NotImplemented — Phase 2]
```

**Factory:** `get_market_data_provider()` in `backend/app/providers/__init__.py`

---

## Static data files

Located in `backend/app/data/`:

| File | Purpose |
|------|---------|
| `nifty50.json` | NIFTY 50 index + 50 constituents manifest |
| `nifty_universe_cache.json` | Cached NIFTY250 symbol list from NSE |
| `backtest_universe.json` | Fixed 15-stock panel for backtest display |
| `recommendation_universe.json` | Engine tuning (filters, pattern boosts, price buckets) — not a fixed stock list |
| `pattern_definitions.json` | Pattern metadata for UI catalog |
| `applicable_rates.json` | Persisted STCG/STT/stamp/brokerage rates (auto-refreshed daily) |
| `bracket_reconcile_state.json` | Runtime: last bracket catch-up and live-poll timestamps (gitignored) |
| `nse_trading_holidays.json` | NSE weekday holidays (2025–2026) for trading-day calendar |

---

## Applicable rates (tax & charges)

Statutory Indian equity delivery rates are loaded from `app/data/applicable_rates.json` when present; otherwise `Settings` / `app.defaults` apply.

**Broker profiles:** Recommendations and applicable rates JSON use **Sharekhan-aligned** brokerage (0.30%/side). **Paper trading trend** compares **Zerodha delivery** (zero brokerage, ₹15.34 DP/scrip on sell) via `broker_delivery_profiles.py`.

**Auto-refresh (Streamlit app open):**

- **First app start of the day** (any time IST), or
- **9:00 AM IST** if the app stayed open overnight and rates are stale

Sources: Zerodha STT support page, ClearTax/Bajaj STCG articles (HTML parse). Stamp duty uses the statutory default unless overridden in config.

**Manual / cron:**

```bash
cd backend && .venv/bin/python -m app.jobs.refresh_applicable_rates
```

Recommended cron (before market open):

```bash
0 9 * * 1-5 cd /path/to/trading/backend && .venv/bin/python -m app.jobs.refresh_applicable_rates
```

Trade tax, budget allocation, and recommendation notes read live rates via `get_applicable_rates()`.

## Universe configuration

| Universe ID | Symbol count | Used by |
|-------------|--------------|---------|
| `NIFTY50` | 51 (index + 50) | Legacy, seed data |
| `NIFTY250` | ~250 | Default sync, backtest, recommendations |
| Backtest panel | 15 | Fixed subset in backtest UI |

Change universes via env vars or JSON files. After changing `MARKET_DATA_UNIVERSE`, run **Refresh market data** to reconcile instruments and backfill.

---

## Startup checklist config

`requirements-start.txt` defines per-session health checks:

| Check | Argument | Required |
|-------|----------|----------|
| `python_version` | 3.11 | yes |
| `venv` | backend/.venv | yes |
| `pip_packages` | requirements-migrate.txt | yes |
| `env_file` | backend/.env | yes |
| `postgres` | localhost:5432 | yes |
| `database` | connect | yes |
| `migrations` | alembic_head | yes |
| `port_free` | 8501 | no |
| `streamlit` | import | yes |

---

## Example `.env` for development

```env
DATABASE_URL=postgresql+asyncpg://trading:trading@localhost:5432/trading
PAPER_STARTING_CASH=1000000
DAILY_TRADING_BUDGET_INR=100000
DATA_PROVIDER=nse
BACKFILL_DAYS=120
MARKET_DATA_UNIVERSE=NIFTY250
DEFAULT_SIMULATION_UNIVERSE=NIFTY250
MAX_TARGET_PROFIT_PCT=80
PAPER_TRADING_RETENTION_DAYS=30
AUDIT_ENABLED=true
AUDIT_LOG_API_REQUESTS=true
AUDIT_TRACEBACK_MAX_CHARS=4000
```

---

## Security notes

- Never commit `backend/.env` (listed in `.gitignore`)
- Rotate Postgres password if exposed
- Sharekhan tokens are sensitive — store only in `.env`

Next: [Patterns & backtesting](10-patterns-and-backtesting.md)

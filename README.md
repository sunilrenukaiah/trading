# NIFTY Paper Trading Simulation Platform

A paper-trading simulation platform for the NIFTY 50 index and its 50 constituent stocks. Tracks 30+ days of OHLCV candles, supports virtual buy/sell with P&L tracking, and is designed for future Sharekhan API integration.

## Features

- **Market data**: NIFTY 50 index + 50 constituent stocks via yfinance (Phase 1)
- **Paper trading**: Market/limit orders, positions, trade history, realized & unrealized P&L; live bracket polling with offline catch-up; chart popups for symbols and open positions
- **Streamlit UI**: Single Python app — no Node.js required; **no AI/LLM APIs** in normal operation
- **Recommendations**: Daily pattern-ranked picks, budget allocation, read-only budget simulation, bracket placement
- **Pluggable data providers**: Swap to Sharekhan when API access is enabled

## Quick Start

**Prerequisites:** Python 3.11+, PostgreSQL

### New machine / Windows migration

See **[docs/MIGRATION.md](docs/MIGRATION.md)** for Cursor + Windows setup.

**Full architecture documentation:** **[docs/project-architecture/](docs/project-architecture/)** — data model, diagrams, API, UI, operations.

```bash
# One-time setup (installs venv, packages, DB migrations, IDE config)
python Setup.py              # Cursor (default)
python Setup.py pycharm      # PyCharm Community Edition

# Every app start (health checks + Streamlit UI)
python scripts/run_app.py
```

Windows: `setup.bat` / `setup-pycharm.bat` and `start.bat`.

Checklist files:

- `requirements-migrate.txt` — one-time Python dependencies (used by `Setup.py`)
- `requirements-start.txt` — per-session service checks (used by `scripts/run_app.py`)

### Manual quick start

```bash
cp backend/env.example backend/.env
# Edit .env if your Postgres credentials differ

cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
streamlit run ui/dashboard.py
```

Open **http://localhost:8501**

On first launch, the app runs migrations, seeds NIFTY 50 instruments, creates a default paper account (₹10,00,000), and backfills 60 days of candle data if the database is empty.

Use **Refresh market data** in the sidebar to pull the latest candles from yfinance.

## Optional: REST API

The FastAPI backend is still available if you want programmatic access:

```bash
cd backend
source .venv/bin/activate
alembic upgrade head
python -m app.bootstrap
uvicorn app.main:app --reload
```

API docs: http://localhost:8000/docs

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://trading:trading@localhost:5432/trading` | PostgreSQL connection string |
| `PAPER_STARTING_CASH` | `1000000` | Initial virtual cash (INR) |
| `DATA_PROVIDER` | `yfinance` | `yfinance` or `sharekhan` |
| `BACKFILL_DAYS` | `60` | Days of history to fetch |

Create the database once:

```bash
createdb -U postgres trading
# or: psql -c "CREATE USER trading WITH PASSWORD 'trading'; CREATE DATABASE trading OWNER trading;"
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/instruments` | List all tracked instruments |
| GET | `/api/instruments/{symbol}/candles?days=30` | OHLCV candles |
| GET | `/api/market/summary` | Latest prices for all instruments |
| GET | `/api/paper/account` | Portfolio summary |
| GET | `/api/paper/positions` | Open positions |
| GET | `/api/paper/orders` | Order history |
| GET | `/api/paper/trades` | Trade ledger |
| POST | `/api/paper/orders` | Place buy/sell order |
| DELETE | `/api/paper/orders/{id}` | Cancel pending limit order |
| POST | `/api/admin/sync` | Trigger data refresh |

### Backtest API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/backtest/patterns` | List all registered patterns |
| POST | `/api/backtest/run` | Run backtest on 15 stocks × 30 days |
| GET | `/api/backtest/latest` | Latest pattern leaderboard |
| GET | `/api/backtest/{run_id}/patterns/{pattern_id}/detail` | Per-stock breakdown |

## Pattern Backtesting

The platform backtests **79 registered patterns** on **NIFTY250** (default) or a fixed **15-stock panel** in the backtest UI, over the last **30 trading days**:

- **Scoring**: For each day, count how many universe stocks the pattern called correctly (e.g. 12/15 on the panel)
- **Validation**: Pattern uses 20-day lookback; actual = close today vs close yesterday
- **Pattern families**: Candlestick (Groww + Fidelity), chart patterns, technical indicators (RSI, MACD, ADX, Stochastic, etc.), Bollinger, price action, combinations

Run via Streamlit: open **Pattern backtest** in the sidebar, or call `POST /api/backtest/run`.

Adding a new pattern: create a file under `backend/app/strategies/patterns/` with `@register_pattern` — no engine changes needed.


When you enable Sharekhan API access:

1. Request API access at [Sharekhan Trading API](https://www.sharekhan.com/trading-api/documentation/overview)
2. Install the official SDK: `pip install shareconnect`
3. Set environment variables:
   ```
   DATA_PROVIDER=sharekhan
   SHAREKHAN_API_KEY=your_key
   SHAREKHAN_CUSTOMER_ID=your_id
   SHAREKHAN_ACCESS_TOKEN=your_token
   ```
4. Implement `SharekhanProvider` in `backend/app/providers/sharekhan_provider.py`:
   - `master("NC")` → map scrip codes to `instruments.sharekhan_scrip_code`
   - `historicaldata(exchange, scripcode, interval)` → replace yfinance candles
   - WebSocket feeds → live quotes for intraday paper trading
5. Add OAuth/login flow for token refresh

The paper trading engine and UI require no changes when switching data providers.

## Project Structure

```
trading/
├── backend/
│   ├── app/
│   │   ├── api/routes/       # REST endpoints (optional)
│   │   ├── data/nifty50.json # NIFTY 50 constituent manifest
│   │   ├── models/           # SQLAlchemy models
│   │   ├── providers/        # yfinance + Sharekhan stub
│   │   ├── services/         # ingestion + paper trading + backtest
│   │   └── strategies/       # pattern registry + 79 backtest patterns
│   ├── ui/                   # Streamlit dashboard
│   └── alembic/              # DB migrations
└── .env.example
```

## Development

### Integration tests

Run before publishing or merging changes:

```bash
cd backend
pip install -e ".[dev]"
./scripts/run_tests.sh all          # full suite (includes post-deploy)
./scripts/run_tests.sh quick        # fast import/UI contract checks only
./scripts/run_tests.sh post_deploy  # API + UI smoke after deploy (needs Postgres)
```

Post-deploy tests hit every GET API route and verify UI modules load without 5xx errors. Against a running server:

```bash
export POST_DEPLOY_API_URL=http://localhost:8000
./scripts/run_tests.sh post_deploy
```

Optional slow mutating checks (backtest run, admin sync):

```bash
POST_DEPLOY_RUN_MUTATING=1 ./scripts/run_tests.sh post_deploy
```

From repo root: `make test`, `make test-quick`, or `make test-post-deploy`.

CI runs the same suite on GitLab (`/.gitlab-ci.yml`) and GitHub Actions (`/.github/workflows/integration-tests.yml`), including a **post-deploy** job with PostgreSQL.

Cursor project hooks (`.cursor/hooks.json`) run quick tests after Python edits under `backend/app` and `backend/ui`, and request an agent fix loop if the full suite fails when a turn completes.

### Audit logging

All major actions are logged to the `audit_logs` table with timing and errors:

- **API requests** — via middleware (path, method, status code, duration)
- **UI jobs** — `job.market_sync`, `job.sim_backtest`, `job.today_prediction`, `job.recommendations`
- **UI services** — `backtest.run`, `prediction.validate_today`, `recommendation.run`
- **Ingestion** — `ingestion.sync_latest`
- **Captured errors** — `log.*`, `sys.unhandled_exception`, `asyncio.unhandled_exception`

Each `audit_track()` operation emits `STARTED` + terminal status with a shared `correlation_id`.

Query recent logs:

```bash
curl "http://localhost:8000/api/admin/audit-logs?limit=50"
curl "http://localhost:8000/api/admin/audit-logs?status=FAILED&action_prefix=job."
```

Config (`.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `AUDIT_ENABLED` | `true` | Master switch |
| `AUDIT_LOG_API_REQUESTS` | `true` | Log each API call |
| `AUDIT_TRACEBACK_MAX_CHARS` | `4000` | Truncate stack traces |

## Paper Trading Rules

- **Market orders** fill at the latest available close price
- **Limit orders** remain pending until price crosses the limit on sync
- **Sells** rejected if insufficient quantity
- **P&L**: unrealized = (mark − avg cost) × qty; realized on sell fills

## License

Private / personal use.

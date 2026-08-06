# Prerequisites

Everything required before installing and running the NIFTY Paper Trading platform.

## Required software

| Component | Minimum version | Purpose |
|-----------|-----------------|---------|
| **Python** | 3.11+ | Backend, Streamlit UI, Alembic migrations, tests |
| **PostgreSQL** | 15+ | Persistent storage for candles, orders, backtests, recommendations |
| **pip** | Bundled with Python | Package installation |
| **Git** | Any recent | Clone and sync the repository (recommended) |

## Optional software

| Component | Purpose |
|-----------|---------|
| **Cursor** or **PyCharm Community** | IDE with auto-generated run configurations via `Setup.py` |
| **pgAdmin** or **psql** | Inspect PostgreSQL databases |
| **Make** | Convenience targets (`make test`, etc.) on macOS/Linux |

## Not required

The following are **not** needed for this project:

- Node.js / npm (React frontend was removed; UI is Streamlit-only)
- Redis, Kafka, or message queues
- Docker (native PostgreSQL is used)
- Sharekhan API credentials (Phase 2; paper mode uses NSE/yfinance)

## Hardware

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| RAM | 4 GB | 8 GB+ |
| Disk | 2 GB free | 5 GB+ (includes venv + candle history) |
| CPU | 2 cores | 4 cores (backtest runs are CPU-bound) |

## Network

| Requirement | Details |
|-------------|---------|
| **Internet** | Required for NSE market data sync and live quotes during market hours |
| **Outbound HTTPS** | NSE endpoints, Yahoo Finance (fallback for index), package installs |
| **Local ports** | **5432** (PostgreSQL), **8501** (Streamlit), **8000** (optional FastAPI) |

No inbound ports need to be exposed beyond localhost for local development.

## Database credentials (default)

The project expects a PostgreSQL role and database:

| Setting | Default |
|---------|---------|
| Host | `localhost` |
| Port | `5432` |
| User | `trading` |
| Password | `trading` |
| Database | `trading` |

Create these before running migrations, or adjust `backend/.env` to match your setup.

## Python packages

Installed automatically by `Setup.py` from `requirements-migrate.txt`:

- **Web:** FastAPI, Uvicorn, Streamlit
- **Database:** SQLAlchemy (async), asyncpg, Alembic
- **Data:** pandas, yfinance, nsefeed, httpx
- **UI charts:** Plotly
- **Testing:** pytest, pytest-asyncio

## Platform-specific notes

### Windows

- Enable **"Add python.exe to PATH"** during Python installation
- Install PostgreSQL from [postgresql.org/download/windows](https://www.postgresql.org/download/windows/)
- Use PowerShell or CMD from the repo root for `Setup.py` and `scripts\run_app.py`
- See [MIGRATION.md](../MIGRATION.md) for full Windows/Cursor guide

### macOS / Linux

- Use system Python 3.11+ or pyenv
- PostgreSQL via Homebrew (`brew install postgresql@15`) or distro package manager
- Activate venv: `source backend/.venv/bin/activate`

## Knowledge prerequisites (for developers)

| Area | Useful for |
|------|------------|
| Python async/await | Understanding services and DB sessions |
| SQLAlchemy 2.x | Models, migrations, queries |
| Streamlit | UI customization |
| Indian equity markets | NSE session hours (9:15–15:30 IST), NIFTY universes |
| Technical analysis basics | Pattern definitions and backtest interpretation |

## Pre-flight checklist

Before first run, confirm:

- [ ] Python 3.11+ available (`python --version`)
- [ ] PostgreSQL running on port 5432
- [ ] `trading` database and user created (or custom `DATABASE_URL` in `.env`)
- [ ] Repo cloned/copied to local machine
- [ ] Internet access for initial market data backfill

Next: [Installation guide](02-installation.md)

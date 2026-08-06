# Installation Guide

Step-by-step setup for macOS, Linux, and Windows.

## Installation paths

| Method | When to use |
|--------|-------------|
| **Automated (`Setup.py`)** | New machine, Windows migration, IDE setup |
| **Manual** | Developers who prefer full control |
| **Existing Postgres** | You already have a database instance |

---

## Method 1: Automated setup (recommended)

From the **repository root** (`trading/`):

```bash
# Cursor (default)
python Setup.py

# PyCharm Community Edition
python Setup.py pycharm
```

Windows shortcuts: double-click `setup.bat` or `setup-pycharm.bat`.

### What Setup.py does

1. Verifies Python 3.11+
2. Creates `backend/.venv`
3. Installs packages from `requirements-migrate.txt`
4. Creates `backend/.env` from `backend/env.example` if missing
5. Runs `alembic upgrade head` (when PostgreSQL is reachable)
6. Writes IDE run configurations (`.vscode/` or `.idea/runConfigurations/`)

If PostgreSQL is not running, migrations are skipped and manual steps are printed.

---

## Method 2: Manual installation

### Step 1 — Clone or copy the project

```bash
git clone <your-repo-url> trading
cd trading
```

### Step 2 — Install PostgreSQL

**macOS (Homebrew):**

```bash
brew install postgresql@15
brew services start postgresql@15
createuser -s trading 2>/dev/null || true
psql -c "ALTER USER trading WITH PASSWORD 'trading';"
createdb -O trading trading
```

**Linux (Debian/Ubuntu):**

```bash
sudo apt install postgresql postgresql-contrib
sudo -u postgres psql -c "CREATE USER trading WITH PASSWORD 'trading';"
sudo -u postgres psql -c "CREATE DATABASE trading OWNER trading;"
```

**Windows:**

1. Install from [postgresql.org/download/windows](https://www.postgresql.org/download/windows/)
2. Start the PostgreSQL service
3. In pgAdmin or psql:

```sql
CREATE USER trading WITH PASSWORD 'trading';
CREATE DATABASE trading OWNER trading;
```

### Step 3 — Python virtual environment

```bash
cd backend
python3.11 -m venv .venv

# macOS/Linux
source .venv/bin/activate

# Windows
.venv\Scripts\activate

pip install --upgrade pip
pip install -r ../requirements-migrate.txt
pip install -e .
pip install -e ".[dev]"   # optional: tests
```

### Step 4 — Environment file

```bash
cp env.example .env
# Edit .env if your Postgres credentials differ
```

Minimum `.env`:

```env
DATABASE_URL=postgresql+asyncpg://trading:trading@localhost:5432/trading
DATA_PROVIDER=nse
BACKFILL_DAYS=120
MARKET_DATA_UNIVERSE=NIFTY250
```

See [Configuration](09-configuration.md) for all variables.

### Step 5 — Database migrations

```bash
cd backend
.venv/bin/python -m alembic upgrade head    # Windows: .venv\Scripts\python
```

Migration chain: `001` → `006` (see [Data model](04-data-model.md)).

### Step 6 — Bootstrap (optional on first UI launch)

The Streamlit app auto-bootstraps on first load:

- Seeds NIFTY instruments
- Creates default paper account
- Backfills OHLCV candles if empty

Or run manually:

```bash
cd backend
.venv/bin/python -m app.bootstrap
```

---

## Starting the application

### Every session (recommended)

```bash
python scripts/run_app.py
```

This runs `requirements-start.txt` health checks, then launches Streamlit at **http://localhost:8501**.

Windows: double-click `start.bat`.

### Health check only

```bash
python scripts/startup_checklist.py
```

### Direct Streamlit (skip startup checks)

```bash
cd backend
source .venv/bin/activate
streamlit run ui/dashboard.py
```

### Optional FastAPI REST API

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

API docs: http://localhost:8000/docs

---

## Post-install verification

```mermaid
flowchart TD
    A[python scripts/run_app.py] --> B{Startup checklist}
    B -->|Pass| C[Streamlit opens :8501]
    B -->|Fail| D[Fix Postgres / venv / migrations]
    C --> E[Trading tab → Refresh market data]
    E --> F[Candles + instruments loaded]
    F --> G[Run integration tests optional]
```

| Check | Expected result |
|-------|-----------------|
| http://localhost:8501 | Streamlit dashboard loads |
| Trading → Refresh market data | Background sync completes |
| Positions / Orders tabs | Empty or show existing paper trades |
| `python scripts/startup_checklist.py` | All required checks pass |

Run tests (optional):

```bash
cd backend
./scripts/run_tests.sh quick     # fast, no DB
./scripts/run_tests.sh all       # full suite (needs Postgres)
```

---

## IDE setup

| IDE | Command | Doc |
|-----|---------|-----|
| Cursor | `python Setup.py cursor` | [MIGRATION.md](../MIGRATION.md) |
| PyCharm CE | `python Setup.py pycharm` | [PYCHARM.md](../PYCHARM.md) |

Open the **`trading`** folder as workspace root (not only `backend`).

Mark `backend` as **Sources Root** in PyCharm so `from app.*` imports resolve.

---

## Upgrade / re-install

```bash
# Refresh Python packages
cd backend && .venv/bin/pip install -r ../requirements-migrate.txt -e .

# Apply new migrations
.venv/bin/python -m alembic upgrade head

# Re-run health check
python scripts/startup_checklist.py
```

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `python` not found | Use `python3` or `py -3.11`; add Python to PATH |
| Postgres connection refused | Start PostgreSQL service; verify `DATABASE_URL` |
| Port 8501 in use | Stop other Streamlit: `pkill -f streamlit` |
| Alembic errors | Ensure DB exists; run migrations from `backend/` |
| Import errors in UI | Restart Streamlit; check `backend` is on PYTHONPATH |
| NSE sync fails | Check internet; retry **Refresh market data** |

Next: [Architecture overview](03-architecture-overview.md)

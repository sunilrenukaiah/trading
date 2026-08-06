# Migrating to Windows (Cursor)

This guide covers moving the **NIFTY Paper Trading** codebase to a Windows laptop with [Cursor](https://cursor.com).

## Software & services

| Component | Required | Purpose |
|-----------|----------|---------|
| **Python 3.11+** | Yes | Backend, Streamlit UI, Alembic migrations |
| **PostgreSQL 15+** | Yes | Candles, orders, recommendations, audit logs |
| **Git** | Recommended | Clone/sync the repository |
| **Cursor** | Optional | IDE — `python Setup.py cursor` |
| **PyCharm Community** | Optional | IDE — `python Setup.py pycharm` — see [PYCHARM.md](PYCHARM.md) |
| **Streamlit** | Via pip | Main UI on **http://localhost:8501** |
| **FastAPI + Uvicorn** | Optional | REST API on **http://localhost:8000** |
| **Internet** | Yes | NSE market data & live quotes (paper trading) |

No Node.js, Redis, or Kafka required.

**Full documentation:** [docs/project-architecture/](project-architecture/) — architecture, data model, data flows, installation, API, UI, operations.

## One-time setup on Windows

1. Install **Python 3.11+** from [python.org](https://www.python.org/downloads/) — check **"Add python.exe to PATH"**.
2. Install **PostgreSQL 15+** from [postgresql.org/download/windows](https://www.postgresql.org/download/windows/).
3. Copy/clone this repo to your laptop (USB, Git, zip, etc.).
4. Open the **`trading`** folder in **Cursor**.
5. From repo root in Terminal (PowerShell or CMD):

```bat
python Setup.py
python Setup.py pycharm
```

Or double-click `setup.bat` (Cursor) / `setup-pycharm.bat` (PyCharm Community).

`Setup.py` will:

- Create `backend\.venv`
- Install packages from `requirements-migrate.txt`
- Create `backend\.env` from `backend\env.example` if missing
- Run `alembic upgrade head` (when PostgreSQL is reachable on localhost:5432)
- Configure IDE (`.vscode/` for Cursor, `.idea/runConfigurations/` for PyCharm)
- Print anything you must do manually

### PyCharm Community Edition

Use `python Setup.py pycharm` and follow **[PYCHARM.md](PYCHARM.md)** to set the interpreter to `backend\.venv\Scripts\python.exe` and mark `backend` as Sources Root.

## Every time you start the app

```bat
python scripts\run_app.py
```

Or double-click `start.bat`.

This runs **`requirements-start.txt`** checks (Python, venv, Postgres, DB connection, migrations), then starts Streamlit.

Startup-only check without launching UI:

```bat
python scripts\startup_checklist.py
```

## Checklist files

| File | When to use |
|------|-------------|
| `requirements-migrate.txt` | One-time: Python packages + comments for system deps |
| `requirements-start.txt` | Every session: service health checks |
| `Setup.py` | Runs migration checklist automatically |
| `scripts/run_app.py` | Runs startup checklist + Streamlit |

## PostgreSQL on Windows

1. Install from [postgresql.org/download/windows](https://www.postgresql.org/download/windows/)
2. Ensure the service is running and listening on port **5432**
3. Create role and database matching `backend\.env`:

```sql
CREATE USER trading WITH PASSWORD 'trading';
CREATE DATABASE trading OWNER trading;
```

Default credentials: user/password/db `trading` / `trading` / `trading`.

## Environment file

Copy and edit if needed:

```bat
copy backend\env.example backend\.env
```

Default `DATABASE_URL`:

```
postgresql+asyncpg://trading:trading@localhost:5432/trading
```

## Optional REST API

```bat
cd backend
.venv\Scripts\activate
uvicorn app.main:app --reload --port 8000
```

Docs: http://localhost:8000/docs

## Cursor tips

- Open **`trading`** as the workspace root (not only `backend`).
- Use the integrated terminal for `Setup.py` and `scripts\run_app.py`.
- Project hooks under `.cursor/hooks.json` run quick tests after edits (optional on Windows).

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `python` not found | Reinstall Python with "Add to PATH", or use `py -3.11 Setup.py` |
| Postgres connection refused | Start the Windows PostgreSQL service; verify `DATABASE_URL` in `backend\.env` |
| Port 8501 in use | Stop other Streamlit instances or change port in `run_app.py` |
| Alembic errors | Ensure Postgres is up, then `cd backend && .venv\Scripts\python -m alembic upgrade head` |
| NSE sync fails | Check internet; NSE may rate-limit — retry **Refresh market data** |

## What gets stored locally

- **PostgreSQL** — all app data (safe to backup with `pg_dump`)
- **`backend\.venv`** — Python environment (recreate with `Setup.py`)
- **`backend\.env`** — secrets/config (do not commit; listed in `.gitignore`)

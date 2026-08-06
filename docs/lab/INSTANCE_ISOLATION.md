# Instance isolation (8501 vs 8502)

Main and lab must never share **code imports**, **virtualenv**, or **database writes**. This document is the contract for local development.

## Two instances

| | Main | Lab |
|---|------|-----|
| **Project** | `trading/` | `trading-lab/` (copy) |
| **UI port** | 8501 | 8502 |
| **Start** | `python scripts/run_app.py` | `python scripts/lab/run_lab_app.py` |
| **`.env`** | No `LAB_MODE` | `LAB_MODE=1`, `LAB_SCHEMA=trading_lab` |
| **PostgreSQL** | `public` schema | `trading_lab` schema (same DB name) |
| **Venv** | `trading/backend/.venv` | `trading-lab/backend/.venv` (separate) |
| **Editable install** | `pip install -e .` → `trading/backend` | `pip install -e .` → `trading-lab/backend` |
| **Promote changes** | — | `python scripts/lab/sync_to_main.py` (manual only) |

## What went wrong before

- Lab `.venv` was a **symlink** to main `.venv`.
- `pip install -e .` in either tree updated the **same** editable mapping.
- Streamlit on **8501** could import `ui.helpers` from **trading-lab** (missing symbols, wrong behavior).
- Recommendation snapshots and paper trades could appear to “bleed” if schema guards were bypassed.

## Guardrails (automated)

1. **Startup checklist** — `instance_guard | main:8501` / `lab:8502` in `requirements-start*.txt`
2. **Launchers** — `run_app.py` clears `LAB_MODE` and sets `TRADING_UI_PORT=8501`
3. **Streamlit** — `ui/instance_guard.py` runs on every dashboard load; aborts if `app`/`ui` resolve outside this backend or `LAB_MODE` mismatches
4. **Lab backup** — `create_backup.py` creates a **dedicated** lab venv (no symlink) and writes lab-only `.env`
5. **Manual check** — `python scripts/instance_guard.py --instance main --port 8501`

## Cache and session data

| Layer | Isolation |
|-------|-----------|
| **PostgreSQL** | Lab uses `trading_lab` schema via `LAB_MODE` + `register_lab_search_path` |
| **Recommendation snapshots** | Rows live in the active schema only |
| **Streamlit session state** | Per browser tab / port (separate processes) |
| **Background job registry** | In-process memory per Streamlit worker |
| **On-disk JSON** (`app/data/*`) | Separate file trees after `create_backup.py` |

There is no shared Redis or global file cache between instances.

## Rules for future localhost apps

- **One venv per app root** — never symlink `.venv` across projects.
- **One editable install target per venv** — verify with `python scripts/instance_guard.py`.
- **One port per instance** — set `TRADING_UI_PORT` in `.env` and launcher.
- **One DB schema (or database) per instance** — lab never writes `public` when `LAB_MODE=1`.
- **No `pip install -e ../other-project`** from a shared venv.
- **Promote lab → main only via** `sync_to_main.py` after tests pass.

## Recovery

**Main imports lab code:**

```bash
cd trading/backend
.venv/bin/pip install -e .
python ../scripts/instance_guard.py --instance main --port 8501
```

**Lab shares main venv:**

```bash
python scripts/lab/create_backup.py --skip-db   # from trading/
# Recreates isolated trading-lab/backend/.venv
```

Then restart each app on its port.

# PyCharm Community Edition (Windows)

Setup with IDE-specific configuration:

```bat
python Setup.py pycharm
```

## Open the project

1. **File → Open** → select the **`trading`** folder (repo root, not only `backend`).
2. Trust the project if prompted.

## Python interpreter (required once)

1. **File → Settings** (Ctrl+Alt+S)
2. **Project: trading → Python Interpreter**
3. **Add Interpreter → Add Local Interpreter → Existing**
4. Select:

```
backend\.venv\Scripts\python.exe
```

5. Apply / OK.

If PyCharm asks to create a venv, cancel and use the existing **`backend\.venv`** created by `Setup.py`.

## Sources root

1. In the Project tool window, right-click **`backend`**
2. **Mark Directory as → Sources Root**

This fixes imports like `from app.config import settings` and `from ui.dashboard import main`.

## Run configurations (auto-generated)

After `python Setup.py pycharm`, use the run dropdown (top right):

| Configuration | Purpose |
|---------------|---------|
| **Streamlit Dashboard** | Startup health check + UI at http://localhost:8501 |
| **Startup Health Check** | Verify Postgres, venv, migrations only |
| **FastAPI Server** | Optional API at http://localhost:8000/docs |

## PostgreSQL

Install PostgreSQL 15+ and create the `trading` user/database (see [MIGRATION.md](MIGRATION.md)). Ensure the service is running on **localhost:5432** before starting the app.

## Troubleshooting

| Issue | Fix |
|-------|-----|
| No module named `app` | Mark `backend` as Sources Root |
| Interpreter not listed | Point to `backend\.venv\Scripts\python.exe` manually |
| Postgres errors on run | Start PostgreSQL, verify `backend\.env`, then run **Startup Health Check** |
| Streamlit port in use | Stop other Streamlit instances or change port in `scripts/run_app.py` |

## Community Edition limits

- No built-in Streamlit plugin required — we use **`scripts/run_app.py`** as the entry script.
- Database tools are basic; use pgAdmin or `psql` for Postgres inspection.

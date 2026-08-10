# Cloud deploy (Streamlit Community Cloud + Neon + GitHub Actions)

Solo / free setup: UI on Streamlit Cloud, Postgres on Neon, scheduled jobs on GitHub Actions.

## Architecture

| Piece | Role |
|-------|------|
| [Streamlit Community Cloud](https://share.streamlit.io/) | UI (`backend/ui/dashboard.py`) |
| [Neon](https://neon.tech/) free Postgres | Candles, paper trades, recommendation snapshots |
| GitHub Actions | Market sync 15:45 & 18:00 IST; recommendations ~18:15 IST (weekdays) |

Streamlit may sleep nights/weekends. Cron still runs in GitHub Actions against Neon even if you never open the app.

## 1. Create Neon database

1. Sign up at https://neon.tech and create a project (any region).
2. Copy the connection string. Convert it for this app:

```text
# Neon often gives:
postgresql://USER:PASSWORD@HOST/DB?sslmode=require

# Use asyncpg for this app:
postgresql+asyncpg://USER:PASSWORD@HOST/DB?ssl=require
```

3. Keep the original Neon URL handy for `psql` / console; the app and jobs need the `+asyncpg` form.

## 2. Streamlit Community Cloud

1. App: repository `sunilrenukaiah/trading`, branch `main`.
2. **Main file path:** `backend/ui/dashboard.py` (forward slashes).
3. **Python version:** **3.11** (Advanced settings) — do not use 3.14.
4. **Secrets** (App settings → Secrets), TOML:

```toml
DATABASE_URL = "postgresql+asyncpg://USER:PASSWORD@HOST/DB?ssl=require"
DATA_PROVIDER = "nse"
# Cron runs in GitHub Actions — do not start NIFTY250 sync inside the UI process
DISABLE_UI_SCHEDULED_SYNC = "1"
```

Optional overrides (same names as `backend/env.example`):

```toml
DAILY_TRADING_BUDGET_INR = 50000
BACKFILL_DAYS = 120
MARKET_DATA_UNIVERSE = "NIFTY250"
```

5. Reboot the app after saving secrets.

Root `requirements.txt` is what Cloud installs. Do not rename it.

## 3. One-time schema migrate

From your laptop (venv + `DATABASE_URL` pointing at Neon):

```powershell
cd c:\Data\trading\trading
$env:DATABASE_URL = "postgresql+asyncpg://USER:PASSWORD@HOST/DB?ssl=require"
.\backend\.venv\Scripts\python.exe scripts\cloud_jobs.py migrate
```

Or rely on the first GitHub Actions run (workflows run migrate before jobs).

## 4. GitHub Actions secrets

1. Repo → **Settings → Secrets and variables → Actions**.
2. New repository secret: **`DATABASE_URL`** = same `postgresql+asyncpg://…?ssl=require` string.
3. Workflows (already in repo):
   - `.github/workflows/cloud-market-sync.yml` — cron 10:15 & 12:30 UTC Mon–Fri (+ manual)
   - `.github/workflows/cloud-recommendations.yml` — cron 12:45 UTC Mon–Fri (+ manual)

4. **Actions → Cloud Market Sync → Run workflow** once with **force** to backfill an empty DB (can take a long time for NIFTY250).
5. After sync finishes, run **Cloud Recommendations** once (or wait for the evening cron).

CLI locally:

```powershell
$env:DATABASE_URL = "postgresql+asyncpg://USER:PASSWORD@HOST/DB?ssl=require"
.\backend\.venv\Scripts\python.exe scripts\cloud_jobs.py market-sync --force
.\backend\.venv\Scripts\python.exe scripts\cloud_jobs.py recommendations
```

## Market data on Cloud

Default provider is **NSE** (`DATA_PROVIDER=nse`). On Streamlit Cloud, NSE often
returns **HTTP 403**; the app then **falls back to yfinance** automatically for
the rest of that process.

You can force Yahoo only:

```toml
DATA_PROVIDER = "yfinance"
```

Keep `nse` (default) for local India IPs — best EOD closes — with Cloud auto-fallback.

## Evening recommendations (tomorrow’s plan)

- **UI:** **Run recommendation analysis** is enabled after **6:00 PM IST** on trading days.
- **GitHub Actions:**
  - `cloud-evening-pipeline.yml` at **18:05 IST** — market sync → recommendations → **email**
  - `cloud-recommendations.yml` at **18:15 IST** — recommendations (+ email if SMTP set)
- After session close, picks target the **next trading day** (`prediction_date`).

### Email setup (free SMTP, e.g. Gmail)

1. Create a Gmail [App Password](https://myaccount.google.com/apppasswords) (2FA required).
2. GitHub → **Settings → Secrets and variables → Actions** — add:

| Secret | Example |
|--------|---------|
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USE_TLS` | `true` |
| `SMTP_USERNAME` | `you@gmail.com` |
| `SMTP_PASSWORD` | app password |
| `EMAIL_FROM` | `you@gmail.com` |
| `EMAIL_TO` | `you@gmail.com` |
| `EMAIL_ENABLED` | `true` |

3. Same keys can go in Streamlit Secrets / local `backend/.env` if you send from the UI later.
4. Test: **Actions → Cloud Evening Pipeline → Run workflow** (after market data exists).

Email body includes budget summary and each allocation line (symbol, shares, buy, stop, target, net P/L).

## 5. Expected behaviour

- **Empty Neon** → first forced sync backfills ~120 days of NSE/yfinance data, then seeds paper account.
- **Trading UI** reads/writes the same Neon DB as Actions.
- **Auto-sync skip:** second daily slot may no-op if post-session data is already present (same rules as local).
- **Non-trading days:** jobs exit 0 without work.
- **Backtests:** keep heavy simulation on your laptop; Cloud/Actions focus on sync + daily recommendations.

## 6. Troubleshooting

| Symptom | Fix |
|---------|-----|
| “PostgreSQL is not running” on Cloud | `DATABASE_URL` missing/wrong in Streamlit Secrets; reboot |
| `ModuleNotFoundError: plotly` | Ensure `requirements.txt` is on `main`; reboot |
| Actions: empty `DATABASE_URL` | Add repo Actions secret |
| Sync fails SSL | Use `?ssl=require` on the asyncpg URL |
| NSE 403 on Cloud | Automatic yfinance fallback (or set `DATA_PROVIDER=yfinance`) |
| Recommendations: no candle data | Run market-sync (force) first |
| Job timeout | NIFTY250 first sync is slow; workflow allows 180 minutes |

## 7. Cost / sleep window

- Neon free + Streamlit free + public-repo Actions ≈ **$0**.
- UI can sleep 21:00–08:45 IST weekdays and all weekend; afternoon crons still update Neon.

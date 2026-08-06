# Data Flows

End-to-end workflows with diagrams for the major system paths.

## 1. Application startup

```mermaid
flowchart TD
    A[python scripts/run_app.py] --> B[startup_checklist.py]
    B --> C{Python 3.11+?}
    C -->|No| X[Exit with error]
    C -->|Yes| D{venv exists?}
    D --> E{Postgres :5432?}
    E -->|No| W[Warn — UI limited]
    E -->|Yes| F{DB connect?}
    F --> G{Alembic at head?}
    G --> H[streamlit run ui/dashboard.py]
    H --> I[_init_app bootstrap]
    I --> J[Migrations + seed instruments]
    J --> K[Backfill candles if empty]
    K --> L[Dashboard ready :8501]
```

**Files:** `scripts/run_app.py`, `scripts/startup_checklist.py`, `backend/ui/dashboard.py`

---

## 2. Market data sync

Triggered by: sidebar **Refresh market data**, `POST /api/admin/sync`, or CLI `python -m app.jobs.sync_market_data`.

```mermaid
sequenceDiagram
    participant UI as Streamlit / API
    participant JOB as background_jobs
    participant ING as ingestion.sync_latest
    participant NU as nifty_universe
    participant PRV as NSE Provider
    participant DB as PostgreSQL
    participant TP as TradePlanService

    UI->>JOB: start_market_sync_job()
    JOB->>ING: sync_latest()
    ING->>NU: reconcile universe symbols
    ING->>DB: upsert instruments
    ING->>PRV: fetch missing OHLCV
    PRV-->>ING: candles
    ING->>DB: upsert ohlcv_candles
    ING->>ING: prune stale candles
    ING->>TP: process_eod(last_trading_day)
    TP->>DB: fill bracket orders vs EOD bars
    ING-->>UI: audit SUCCESS
```

**Key behaviors:**

- Universe resolved from `MARKET_DATA_UNIVERSE` (default `NIFTY250`) — same set used for recommendations, backtests, and sync
- Provider selected by `DATA_PROVIDER` (`nse` default)
- After sync, EOD bracket processing runs for the last completed trading day
- Entire operation audited as `ingestion.sync_latest`

---

## 3. Paper trading (manual orders)

```mermaid
flowchart LR
    A[User: sidebar order form] --> B[PaperTradingService.place_order]
    B --> C{Order type?}
    C -->|MARKET| D[Fill at latest close/LTP]
    C -->|LIMIT| E[Status PENDING]
    D --> F[Update cash + position]
    F --> G[Create PaperTrade]
    E --> H[Wait for sync or live match]
    G --> I[(paper_orders<br/>paper_positions<br/>paper_trades)]
    H --> I
```

**Budget check:** BUY orders validated against `daily_trading_budget_inr` via `budget_portfolio.validate_buy_against_budget()`.

**Sell validation:** Rejected if insufficient position quantity.

---

## 4. Bracket trade plan lifecycle

Created from **Recommendations** tab → **Place trade** / **Place order for all**.

```mermaid
stateDiagram-v2
    [*] --> PENDING_ENTRY: place_recommendation_plan()
    PENDING_ENTRY --> OPEN: Entry filled (LTP ≤ limit or EOD low)
    OPEN --> TARGET_HIT: Price ≥ target
    OPEN --> STOP_HIT: Price ≤ stop
    OPEN --> TIME_EXIT: 3:25 PM square-off
    PENDING_ENTRY --> CANCELLED: User cancel
    OPEN --> CANCELLED: User cancel
    TARGET_HIT --> [*]
    STOP_HIT --> [*]
    TIME_EXIT --> [*]
    CANCELLED --> [*]
```

### Intraday (live polling)

```mermaid
sequenceDiagram
    participant FR as st.fragment 10s
    participant LQ as live_quotes
    participant NSE as NSE Quote API
    participant TP as TradePlanService
    participant PT as PaperTradingService
    participant DB as PostgreSQL

    loop Every 10s (market hours)
        FR->>LQ: fetch LTP for open positions
        LQ->>NSE: quote requests
        NSE-->>LQ: last prices
        LQ->>LQ: merge_poll_extremes (session high/low)
        LQ->>TP: process_live_quotes(quotes)
        TP->>DB: entry/exit fills
        alt At or after 3:25 PM IST
            LQ->>PT: square_off_remaining_positions(quotes)
            PT->>DB: market-sell manual holdings
        end
    end
```

Live polling only runs when:

- Toggle **Live polling (10s)** is ON
- Current time is within NSE session (9:15–16:30 IST)
- Day is a trading day

### Bracket catch-up (manual)

```mermaid
sequenceDiagram
    participant UI as Trading Positions view
    participant H as ui/helpers
    participant TP as TradePlanService
    participant NSE as NSE / live quotes
    participant DB as PostgreSQL

    UI->>H: Reconcile brackets button
    H->>H: _reconcile_brackets_if_needed(force=True)
    H->>TP: reconcile_session_brackets_after_downtime()
    TP->>DB: match pending limits; sync entries
    TP->>NSE: session OHLC + live quotes
    TP->>DB: target/stop/entry fills; stale EOD close
    H->>H: record_reconcile_success()
```

Automatic reconcile on Trading tab load was removed for performance — use the manual button or CLI job.

### Orders tab (session view + cleanup)

```mermaid
sequenceDiagram
    participant UI as Trading → Orders tab
    participant H as ui/helpers
    participant TP as TradePlanService
    participant PT as PaperTradingService
    participant DB as PostgreSQL

    UI->>H: _cleanup_duplicate_session_orders()
    H->>TP: cleanup_duplicate_session_plans()
    TP->>DB: cancel duplicate plans/orders; undo duplicate fills
    UI->>H: _orders(session_date=today)
    H->>PT: list_orders (exclude CANCELLED in UI)
    UI->>H: _load_order_bracket_context()
    H->>DB: bracket target buy/sell per plan
    opt Live polling + market hours
        UI->>H: _fetch_live_quotes(symbols)
    end
```

---

## 5. Pattern backtest

```mermaid
flowchart TB
    A[User: Hard refresh or API POST] --> B[BacktestEngine.run]
    B --> C[Load candles from DB]
    C --> D[For each pattern × symbol × day]
    D --> E[Pattern.signal vs actual direction]
    E --> F[Aggregate scores]
    F --> G[(backtest_runs<br/>pattern_scores<br/>stock_scores)]
    G --> H[simulation_cache.save_daily_simulation]
    H --> I[UI leaderboard + detail views]
```

**Scoring example:** On a given day, if 12 of 15 stocks match the pattern's predicted direction, daily score = 12/15.

**Cache:** One cached snapshot per `(simulation_date, universe)` in `report_payload`.

---

## 6. Recommendation engine

```mermaid
flowchart TB
    A[Run analysis] --> B[run_recommendation_engine]
    B --> C[Rank patterns over 15-day window]
    C --> D[Select tier + price-bucket stocks]
    D --> E[allocate_budget]
    E --> F[Build report payload]
    F --> G[save_recommendation_snapshot]
    G --> H[(recommendation_snapshots)]
    H --> I[UI: auto-load on tab visit]
    I --> J[User: Place bracket trades]
    J --> K[(paper_trade_plans)]
    J --> L{Active plan for symbol<br/>this session?}
    L -->|No| M[LIMIT BUY + plan]
    L -->|Yes| N[Skip / show Order placed]
```

**Duplicate prevention:** `place_recommendation_plan()` consults `_find_active_session_plan()` before creating orders. **Place order for all** passes only symbols not in `_load_allocation_trade_plan_state()`.

**Cleanup:** Opening Trading → Orders runs `cleanup_duplicate_session_plans()` to remove legacy duplicate bracket entries from accidental double placement.

---

## 6.1 Mid-day recommendation analysis

```mermaid
sequenceDiagram
    participant UI as Mid-day tab
    participant JOB as MIDDAY_RECOMMENDATIONS job
    participant MS as midday_market_sync
    participant NSE as NSE quotes
    participant RE as recommendation_engine
    participant BA as budget_allocator
    participant RC as recommendation_cache JSON
    participant TP as TradePlanService

    UI->>UI: _midday_budget_context() available base budget
    UI->>JOB: start_midday_recommendations_job(available, max_target)
    JOB->>MS: upsert_intraday_session_candles (250 symbols)
    MS->>NSE: session OHLC per symbol
    MS->>MS: upsert ohlcv_candles
    JOB->>RE: run_recommendation_engine
    JOB->>BA: allocate_budget(available)
    JOB->>RC: save_midday_recommendation_snapshot
    RC-->>UI: auto-load on tab revisit
    UI->>TP: apply_midday_recommendation on Place order
```

**Budget:** `available = morning_budget − invested_open − |realized_P&L_today|` — profits are not reinvested (`compute_base_budget_available`).

**Morning comparison:** Loads DB morning snapshot via `load_cached_recommendations_for_ui()`; does not overwrite it.

---

## 7. EOD analysis

Two levels of EOD reporting:

| Level | Source | UI location |
|-------|--------|-------------|
| Basic | `TradePlanService.build_eod_report()` | Trading tab snippet |
| Rich | `EodTradeAnalysisService` | Analysis & EOD tab |

```mermaid
flowchart LR
    A[Select trade date] --> B[Load trade plans + OHLCV]
    B --> C[Check target/stop touches]
    C --> D[Compute missed targets]
    D --> E[Find alternative patterns]
    E --> F[Reasoning insights + tomorrow actions]
    F --> G[Display tables + expanders]
```

Uses historical bars to determine whether intraday high/low touched target or stop, and whether close beat target.

**As-of date (intraday):** On the **Trading** tab, when the market session is still in progress, EOD snippets pass `as_of_date=active_market_session_date()` so metrics reflect today's session rather than the last *completed* trading day. The **Analysis & EOD** tab uses the selected trade date directly (`eval_date = as_of_date or trade_date` in `EodTradeAnalysisService.build_report()`).

After **3:45 PM IST** on the trade date, the report includes **NIFTY250 — profitable closes not recommended**: universe stocks that closed up but were not in that day's recommendation picks (`EodTradeAnalysisService` + `recommended_symbols_for_prediction_date()`).

---

## 8. Audit logging

```mermaid
flowchart LR
    subgraph sources [Event Sources]
        API[FastAPI Middleware]
        UI[Background Jobs]
        SVC[Service audit_track]
        LOG[ERROR stdlib logs]
        EXC[Unhandled exceptions]
    end

    subgraph dispatch [Non-blocking dispatch]
        SCH[schedule_audit_event]
    end

    subgraph store [Storage]
        AL[(audit_logs)]
        STDERR[app.audit logger]
    end

    API --> SCH
    UI --> SCH
    SVC --> SCH
    LOG --> SCH
    EXC --> SCH
    SCH --> AL
    SCH --> STDERR

    AL --> Q[GET /api/admin/audit-logs]
```

Every HTTP request (when enabled), market sync, backtest run, and recommendation job writes an audit row with duration, status, and optional traceback. Dispatch is **fire-and-forget** — persistence failures are logged to stderr only.

---

## 9. FastAPI request path (optional)

```mermaid
sequenceDiagram
    participant C as Client
    participant M as AuditMiddleware
    participant R as Route Handler
    participant S as Service
    participant DB as PostgreSQL

    C->>M: HTTP request
    M->>R: dispatch
    R->>S: business call
    S->>DB: async query
    DB-->>S: data
    S-->>R: response model
    R-->>M: JSON
    M->>DB: audit log
    M-->>C: HTTP response
```

---

## Flow summary table

| Flow | Trigger | Primary service | Output |
|------|---------|-----------------|--------|
| Startup | `run_app.py` | bootstrap, alembic | Running UI |
| Market sync | Refresh button / API / CLI | `ingestion.sync_latest` | Updated candles; optional paper history prune |
| Manual order | Sidebar form | `PaperTradingService` | Order + position |
| Bracket plan | Recommendations / Mid-day | `TradePlanService` | Trade plan row |
| Live fills | 10s polling | `process_live_quotes` + poll session high/low + `square_off_remaining_positions` | Entry/exit orders; EOD square-off |
| EOD fills | Post-sync | `process_eod` | Bracket completions + remaining holdings at close |
| Backtest | Hard refresh | `BacktestEngine` | Score tables |
| Recommendations | Run analysis | `recommendation_engine` | Snapshot JSON |
| Mid-day analysis | Mid-day tab (11:45+ IST) | `midday_market_sync` + engine | JSON file cache |
| EOD report | Tab select | `EodTradeAnalysisService` | Analysis report |
| Paper trend | Paper trading trend tab | `PaperTradingTrendService` | Daily/cumulative P&L report |

Next: [Services reference](06-services-reference.md)

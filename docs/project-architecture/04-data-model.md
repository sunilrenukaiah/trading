# Data Model

PostgreSQL schema managed by SQLAlchemy 2.x models and Alembic migrations.

## Entity-relationship diagram

```mermaid
erDiagram
    instruments ||--o{ ohlcv_candles : has
    instruments ||--o{ paper_orders : traded
    instruments ||--o{ paper_positions : held
    instruments ||--o{ paper_trade_plans : planned

    paper_accounts ||--o{ paper_orders : places
    paper_accounts ||--o{ paper_positions : holds
    paper_accounts ||--o{ paper_trades : executes
    paper_accounts ||--o{ paper_trade_plans : owns

    paper_orders ||--o| paper_trades : fills
    paper_trade_plans }o--o| paper_orders : entry_order
    paper_trade_plans }o--o| paper_orders : exit_order

    backtest_runs ||--o{ backtest_pattern_scores : contains
    backtest_runs ||--o{ backtest_stock_scores : contains

    instruments {
        int id PK
        string symbol UK
        string name
        enum instrument_type
        string yfinance_symbol
        bool is_nifty50
        bool is_active
    }

    ohlcv_candles {
        int id PK
        int instrument_id FK
        date trade_date
        numeric open
        numeric high
        numeric low
        numeric close
        int volume
        string source
    }

    paper_accounts {
        int id PK
        string name
        numeric initial_cash
        numeric cash_balance
    }

    paper_orders {
        int id PK
        int account_id FK
        int instrument_id FK
        enum side
        enum order_type
        enum status
        int quantity
        numeric limit_price
    }

    paper_positions {
        int id PK
        int account_id FK
        int instrument_id FK
        int quantity
        numeric avg_cost
    }

    paper_trades {
        int id PK
        int order_id FK UK
        int account_id FK
        numeric price
        numeric realized_pnl
    }

    paper_trade_plans {
        int id PK
        int account_id FK
        int instrument_id FK
        date recommendation_date
        enum status
        numeric entry_limit_price
        numeric target_price
        numeric stop_loss_price
    }

    recommendation_snapshots {
        int id PK
        date analysis_date UK
        date prediction_date
        json payload
    }

    backtest_runs {
        int id PK
        date simulation_date
        string universe
        json report_payload
    }

    audit_logs {
        int id PK
        string action
        string component
        string status
        json context
    }
```

## Enums

| Enum | PostgreSQL name | Values |
|------|-----------------|--------|
| `InstrumentType` | `instrument_type` | `INDEX`, `EQUITY` |
| `OrderSide` | `order_side` / `trade_side` | `BUY`, `SELL` |
| `OrderType` | `order_type` | `MARKET`, `LIMIT` |
| `OrderStatus` | `order_status` | `PENDING`, `FILLED`, `CANCELLED`, `REJECTED` |
| `TradePlanStatus` | `trade_plan_status` | `PENDING_ENTRY`, `OPEN`, `TARGET_HIT`, `STOP_HIT`, `TIME_EXIT`, `CANCELLED` |

## Tables reference

### `instruments`

Master list of tracked symbols (NIFTY index + equities).

| Column | Type | Notes |
|--------|------|-------|
| `id` | Integer PK | |
| `symbol` | String(32) UNIQUE | e.g. `TCS`, `NIFTY50` |
| `name` | String(128) | Display name |
| `exchange` | String(8) | Default `NSE` |
| `instrument_type` | Enum | `INDEX` or `EQUITY` |
| `yfinance_symbol` | String(32) | e.g. `TCS.NS`, `^NSEI` |
| `sharekhan_scrip_code` | Integer nullable | Phase 2 broker mapping |
| `is_nifty50` | Boolean | Legacy NIFTY50 flag |
| `is_active` | Boolean | Included in sync when true |

**Source:** `backend/app/models/__init__.py` — class `Instrument`

---

### `ohlcv_candles`

Daily OHLCV bars per instrument.

| Column | Type | Notes |
|--------|------|-------|
| `instrument_id` + `trade_date` | UNIQUE | One bar per day per symbol |
| `open/high/low/close` | Numeric(18,4) | |
| `volume` | Integer | |
| `source` | String(32) | e.g. `nse`, `yfinance` |
| `synced_at` | Timestamptz | Last update time |

**Index:** `ix_candles_instrument_date (instrument_id, trade_date)`

---

### `paper_accounts`

Virtual brokerage accounts for paper trading.

| Column | Type | Notes |
|--------|------|-------|
| `initial_cash` | Numeric(18,2) | Starting balance |
| `cash_balance` | Numeric(18,2) | Current available cash |

One default account is created at bootstrap.

---

### `paper_orders`

Buy/sell orders (market or limit).

| Column | Type | Notes |
|--------|------|-------|
| `side` | Enum | `BUY` / `SELL` |
| `order_type` | Enum | `MARKET` / `LIMIT` |
| `status` | Enum | Lifecycle state |
| `limit_price` | Numeric nullable | Required for LIMIT |
| `filled_price` / `filled_at` | nullable | Set on fill |

---

### `paper_positions`

Open holdings (one row per account + instrument).

| Column | Type | Notes |
|--------|------|-------|
| `quantity` | Integer | Shares held |
| `avg_cost` | Numeric(18,4) | Weighted average entry |

**Unique:** `(account_id, instrument_id)`

**Note:** Position **source** (Manual vs Recommendation) is not stored on this table. It is derived at read time in `PaperTradingService.list_positions()` by checking for an active `PaperTradePlan` (`PENDING_ENTRY` or `OPEN`) for the symbol. Exposed on the API/UI as `PositionOut.source` (`PositionSource` enum).

---

### `paper_trades`

Executed trade ledger (1:1 with filled orders).

| Column | Type | Notes |
|--------|------|-------|
| `order_id` | FK UNIQUE | Links to originating order |
| `realized_pnl` | Numeric(18,2) | P&L on SELL fills |

---

### `paper_trade_plans`

Bracket trade plans from recommendations (limit entry + target + stop).

| Column | Type | Notes |
|--------|------|-------|
| `recommendation_date` | Date | Day plan was created |
| `entry_limit_price` | Numeric | Limit buy price |
| `target_price` / `stop_loss_price` | Numeric | Exit levels |
| `status` | Enum | Plan lifecycle |
| `entry_order_id` / `exit_order_id` | FK nullable | Linked paper orders |
| `pattern_name` | String nullable | Source pattern |

**Unique:** `(account_id, instrument_id, recommendation_date)` — one plan per symbol per day

**Index:** `ix_trade_plans_status_rec_date (status, recommendation_date)`

---

### `recommendation_snapshots`

Cached daily recommendation reports (JSON blob).

| Column | Type | Notes |
|--------|------|-------|
| `analysis_date` | Date UNIQUE | Day analysis was run |
| `prediction_date` | Date | Next trading day target |
| `budget_inr` | Numeric | Budget used |
| `max_target_profit_pct` | Numeric | Target cap |
| `payload` | JSON | Full report + allocation lines |

---

### `backtest_runs`

Header row for each backtest execution.

| Column | Type | Notes |
|--------|------|-------|
| `eval_days` | Integer | Evaluation window (e.g. 30) |
| `lookback_days` | Integer | Pattern lookback (e.g. 20) |
| `stock_count` | Integer | Symbols evaluated |
| `universe` | String(32) | e.g. `NIFTY250` |
| `simulation_date` | Date | As-of date for simulation |
| `report_payload` | JSON nullable | Cached daily sim snapshot |

---

### `backtest_pattern_scores`

Aggregate pattern performance per run.

| Column | Type | Notes |
|--------|------|-------|
| `pattern_id` / `pattern_name` | String | Registry identifiers |
| `total_correct` / `total_signals` | Integer | Hit counts |
| `avg_daily_score` | Numeric | Avg stocks correct per day |
| `overall_hit_rate` | Numeric | Percentage |
| `rank` | Integer | Leaderboard position |

---

### `backtest_stock_scores`

Per-pattern, per-symbol breakdown.

| Column | Type | Notes |
|--------|------|-------|
| `symbol` | String(32) | Stock symbol |
| `correct` / `signals` | Integer | |
| `hit_rate` | Numeric | |

---

### `audit_logs`

Structured audit trail for API, jobs, and services.

| Column | Type | Notes |
|--------|------|-------|
| `action` | String(128) indexed | e.g. `ingestion.sync_latest` |
| `component` | String(32) indexed | e.g. `api`, `ui`, `service`, `job`, `ingestion` |
| `status` | String(16) indexed | `STARTED`, `SUCCESS`, `FAILED`, `CLIENT_ERROR`, `SKIPPED` |
| `duration_ms` | Integer nullable | Execution time |
| `message` | Text nullable | Human-readable summary |
| `error_type` / `error_message` | nullable | On failure |
| `context` | JSON nullable | Structured metadata |
| `session_id` / `request_id` / `correlation_id` | String(64) nullable | Tracing — `correlation_id` links STARTED/SUCCESS/FAILED for one operation |
| `traceback` | Text nullable | Truncated stack trace |

**Source:** `backend/app/models/audit_log.py`

## Migration history

| Revision | File | Changes |
|----------|------|---------|
| **001** | `001_initial_schema.py` | Core tables: instruments, candles, paper trading |
| **002** | `002_backtest_results.py` | backtest_runs, pattern_scores, stock_scores |
| **003** | `003_daily_simulation_cache.py` | universe, simulation_date, report_payload on runs |
| **004** | `004_audit_logs.py` | audit_logs table + indexes |
| **005** | `005_paper_trade_plans.py` | paper_trade_plans + trade_plan_status enum |
| **006** | `006_recommendation_snapshots.py` | recommendation_snapshots |
| **007** | `007_trade_plan_time_exit.py` | Add `TIME_EXIT` to trade_plan_status enum |

Apply all:

```bash
cd backend && .venv/bin/python -m alembic upgrade head
```

## JSON payload shapes (informal)

### `recommendation_snapshots.payload`

Contains serialized recommendation report: ranked patterns, tier picks, bucket allocations, budget lines, metadata timestamps. Produced by `recommendation_cache.save_recommendation_snapshot()`.

### `backtest_runs.report_payload`

Daily simulation cache: validation matrices, bullish/bearish scores, pattern leaderboard snapshot. Managed by `simulation_cache.py`.

## Data retention

| Data | Retention policy |
|------|------------------|
| OHLCV candles | Rolling window per `BACKFILL_DAYS` (default 120); stale bars pruned on sync |
| Backtest runs | Accumulates; no auto-prune |
| Audit logs | Accumulates; query via admin API |
| Paper trades/orders/plans | **30-day rolling retention** (`PAPER_TRADING_RETENTION_DAYS`); pruned nightly after 3:45 PM IST on market sync |

### UI vs database scope

| Surface | Scope |
|---------|-------|
| Trading → Orders / Trades tabs | **Current IST session date only** (`list_orders(session_date)`, `list_trades(session_date)`); **cancelled orders hidden** in UI |
| Paper trading trend tab | **Last 30 days** of closed trades (charts/tables); portfolio snapshot is current |
| Positions tab | Current open holdings; **summary metrics** (total unrealized P&L, profit/loss stock counts) |

## API schemas (non-persistent)

### `PositionOut` / `PositionSource`

Defined in `backend/app/schemas/__init__.py` — not a database column.

| Field / enum | Values | Meaning |
|--------------|--------|---------|
| `PositionSource.RECOMMENDATION` | `RECOMMENDATION` | Holding linked to an active bracket plan from Recommendations |
| `PositionSource.MANUAL` | `MANUAL` | Holding from Trading tab sidebar **Place order** only |
| `PositionOut.source` | enum | Set when listing positions for UI badges |

### `BudgetAllocationReport` / `AllocationLine` (runtime, not persisted)

Produced by `budget_allocator.allocate_budget()` — not stored as separate DB rows; allocation lines are embedded in `recommendation_snapshots.payload`.

| Field | Type | Notes |
|-------|------|-------|
| `BudgetAllocationReport.lines` | `list[AllocationLine]` | Per-symbol share counts, investment, target/stop, tax-adjusted profit |
| `BudgetAllocationReport.skipped_invalid` | `list[str]` | Symbols skipped because target ≤ entry (invalid bracket) |
| `BudgetAllocationReport.backfilled_symbols` | `list[str]` | Alternate picks substituted from the same tier when primaries were invalid |
| `AllocationLine.shares` | int | Integer shares at recommended buy price |
| `AllocationLine.actual_sell_price` | float | Tax-adjusted sell target used for bracket placement |

**Bracket validation:** `bracket_utils.is_valid_bracket_levels(buy, target, stop)` requires `stop < buy < target`. Used by allocator, UI place-order guards, and `place_recommendation_plan()`.

Next: [Data flows](05-data-flows.md)

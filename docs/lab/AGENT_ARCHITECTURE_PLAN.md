# Agent architecture rollout (lab-only until sync)

This document describes how we migrate from the single-process Streamlit model to **parallel worker agents**, with all experiments isolated in **trading-lab** until you run `sync`.

## Isolation model

| Item | Main (`trading`) | Lab (`trading-lab`) |
|------|------------------|---------------------|
| Code | Production — untouched during experiments | All architecture changes |
| Database | `trading` schema **public** | Same DB, schema **`trading_lab`** (cloned copy) |
| Streamlit | http://localhost:**8501** | http://localhost:**8502** |
| Sync | — | `python scripts/lab/sync_to_main.py` when you approve |

### Commands

```bash
# From main repo — create / refresh backup (code + DB)
python scripts/lab/create_backup.py

# Start lab UI on port 8502
python scripts/lab/run_lab_app.py

# Verify lab (full test suite)
python scripts/lab/verify_lab.py

# Promote lab -> main (after you say "sync")
python scripts/lab/sync_to_main.py --yes
# Optional: also copy lab DB over main DB
python scripts/lab/sync_to_main.py --yes --include-db
```

---

## Rollout phases

Each phase is implemented **only in trading-lab**, then:

1. `./scripts/run_tests.sh all` in lab
2. Smoke test on http://localhost:8502
3. You review results
4. When ready: `sync_to_main.py`

### Phase 0 — Lab infrastructure ✅

- [x] Code copy (rsync, excludes `.venv`)
- [x] PostgreSQL clone → `trading_lab`
- [x] Lab `.env` with `LAB_MODE=1`, separate `DATABASE_URL`
- [x] Lab Streamlit on port **8502**
- [x] `sync_to_main.py` with test gate

**Test gate:** `verify_lab.py` → 271 tests pass

---

### Phase 1 — Persistent job queue (replace in-memory jobs)

**Goal:** Jobs survive tab switches and can be processed outside Streamlit.

| Task | Detail |
|------|--------|
| Add table | `background_jobs` (id, kind, status, progress, message, payload, result, session_key, timestamps) |
| Alembic migration | In lab only first |
| Service | `app/services/job_queue.py` — enqueue, update, poll, complete |
| UI bridge | `background_jobs.py` writes to DB instead of `_jobs_by_session` dict |
| Worker stub | `app/jobs/worker.py` — poll queue, dispatch by `JobKind` |

**Parallelism unlocked:** UI enqueues; worker executes (still one worker initially).

**Tests:**
- Unit: enqueue/update/complete lifecycle
- Integration: job progress visible while Trading tab loads DB
- Regression: all 271 existing tests pass

---

### Phase 2 — Separate worker process

**Goal:** CPU work no longer shares Streamlit’s async DB lock.

| Task | Detail |
|------|--------|
| CLI entry | `python -m app.jobs.worker --poll-interval 2` |
| Own event loop | Uses `app.db.session` pool, not `ui_session` |
| Lab script | `scripts/lab/run_lab_worker.py` |
| Streamlit | Only enqueues + reads job row from DB |

**Tests:**
- Start worker + lab UI; run simulation; UI stays responsive
- Worker crash → job marked failed; re-queue safe

---

### Phase 3 — Simulation agent (first compute offload)

**Goal:** `SIM_BACKTEST` runs entirely in worker.

| Task | Detail |
|------|--------|
| Extract | `BacktestEngine.run` → job handler |
| Progress | Worker updates `background_jobs.message` / `progress` |
| Cache | Unchanged — `simulation_cache` |
| Remove mutex | Allow `MARKET_SYNC` queue while sim runs (read-only phase) |

**Tests:**
- Hard refresh simulation in lab
- Compare `simulation_cache` payload hash vs Phase 0 baseline (same inputs → same rankings)

---

### Phase 4 — Recommendation agent

**Goal:** `RECOMMENDATIONS` job in worker with phased progress (already wired).

| Task | Detail |
|------|--------|
| Handler | `run_recommendation_analysis` in worker |
| Dependency | Optional: require fresh OHLCV flag in job payload |
| Output | `recommendation_snapshots` unchanged |

**Tests:**
- Run analysis in lab; verify snapshot `prediction_date`
- Trading tab EOD loads correct session

---

### Phase 5 — Market sync agent

**Goal:** Long NSE backfill off UI thread.

| Task | Detail |
|------|--------|
| Handler | `ingestion.sync_latest` in worker |
| Chain | On success, optionally enqueue sim + recommendations |
| Conflict | Serialize writes to `ohlcv_candles` (one sync at a time) |

**Tests:**
- Integration: `test_simulation_backfill.py`
- Post-deploy smoke (if DB available)

---

### Phase 6 — EOD precompute agent

**Goal:** Rich EOD report pre-built after 3:45 PM IST.

| Task | Detail |
|------|--------|
| Cache table | `eod_analysis_cache` (trade_date, payload JSON) |
| Handler | `EodTradeAnalysisService.build_report` |
| UI | Analysis & EOD tab reads cache first |

**Tests:**
- `test_eod_trade_analysis.py` full suite
- Missed-profitable section only after cutoff

---

### Phase 7 — Parallel workers (optional scale-out)

**Goal:** Shard CPU-bound loops across processes.

| Job | Shard key | Merge |
|-----|-----------|-------|
| Simulation | Symbol batches | Sum pattern scores |
| Recommendation rank | Symbol batches | Same as backtest merge |
| EOD missed scan | Symbol batches | Concatenate rows |

**Tests:**
- Single-shard vs multi-shard → identical merged report
- Load test: 2 workers, 2 job types queued

---

### Phase 8 — Sync to main & cutover

When you say **sync**:

```bash
python scripts/lab/sync_to_main.py --yes
# restart main
python scripts/run_app.py
```

Optional production worker:

```bash
cd backend && .venv/bin/python -m app.jobs.worker
```

---

## What stays in Streamlit (never agentized)

- Live quote polling (10s) + poll session high/low + bracket fills
- Manual orders
- Charts and read-only portfolio views

---

## Risk controls

| Risk | Mitigation |
|------|------------|
| Lab breaks main | Code changes only in `trading-lab` until sync; see [INSTANCE_ISOLATION.md](./INSTANCE_ISOLATION.md) |
| DB corruption | Separate `trading_lab`; main DB untouched |
| Bad sync | `sync_to_main.py` runs lab verify + main tests before finish |
| Job double-run | Idempotent job ids + unique constraints on cache keys |

---

## Current status

- **Phase 0:** tooling ready — run `create_backup.py` to materialize lab
- **Phases 1–7:** pending — each gated by tests + your review on port 8502

"""Background jobs for Streamlit — survive tab switches and page reruns."""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from datetime import timedelta
from enum import Enum
from typing import Any, Callable

import streamlit as st

from ui import job_registry

_lock = job_registry._lock


STALE_JOB_SECONDS = 45 * 60  # warn after 45 min with no heartbeat


class JobKind(str, Enum):
    SIM_BACKTEST = "sim_backtest"
    TODAY_PREDICTION = "today_prediction"
    RECOMMENDATIONS = "recommendations"
    MIDDAY_RECOMMENDATIONS = "midday_recommendations"
    MARKET_SYNC = "market_sync"


def is_any_job_running() -> bool:
    return bool(list_jobs())


def _jobs_for_session(session_key: str) -> dict[str, dict[str, Any]]:
    return job_registry.jobs_for_session(session_key)


def _session_key() -> str:
    """Stable browser session key — survives Streamlit reruns and transient ctx loss."""
    prev: str | None = None
    try:
        prev = st.session_state.get("_job_session_key")
    except Exception:
        prev = None

    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx

        ctx = get_script_run_ctx()
        if ctx and ctx.session_id:
            key = ctx.session_id
            if prev and prev != key:
                job_registry.migrate_session_jobs(prev, key)
            try:
                st.session_state["_job_session_key"] = key
            except Exception:
                pass
            return key
    except Exception:
        pass

    if prev:
        return prev
    try:
        st.session_state["_job_session_key"] = "default"
    except Exception:
        pass
    return "default"


def _session_jobs() -> dict[str, dict[str, Any]]:
    key = _session_key()
    with _lock:
        bucket = _jobs_for_session(key)
        if bucket:
            return bucket
        if key == "default":
            return bucket
        default_bucket = job_registry.all_session_jobs().get("default")
        if not default_bucket:
            return bucket
        running = {
            jid: job
            for jid, job in default_bucket.items()
            if job.get("status") == "running"
        }
        if not running:
            return bucket
        target = _jobs_for_session(key)
        for jid, job in running.items():
            job["session_key"] = key
            target[jid] = job
            default_bucket.pop(jid, None)
        return target


def _update_job(job_id: str, session_key: str, **fields: Any) -> None:
    """Update job state. session_key must be captured on the main thread when the job starts."""
    job_registry.update_job(job_id, session_key, **fields)


def is_kind_running(kind: JobKind) -> bool:
    with _lock:
        return any(
            job["kind"] == kind.value and job["status"] == "running"
            for job in _session_jobs().values()
        )


def get_job(job_id: str) -> dict[str, Any] | None:
    with _lock:
        return _session_jobs().get(job_id)


def list_jobs(*, include_completed: bool = False) -> list[dict[str, Any]]:
    with _lock:
        jobs = list(_session_jobs().values())
    if include_completed:
        return sorted(jobs, key=lambda j: j.get("started_at", 0), reverse=True)
    return [j for j in jobs if j["status"] == "running"]


def cancel_running_job(kind: JobKind) -> bool:
    """Mark a running job as cancelled (worker may still finish; UI stops waiting)."""
    session_key = _session_key()
    with _lock:
        jobs = _jobs_for_session(session_key)
        for job in jobs.values():
            if job["kind"] == kind.value and job["status"] == "running":
                job["status"] = "failed"
                job["error"] = "Cancelled by user"
                job["message"] = "Cancelled"
                job["synced"] = False
                job["updated_at"] = time.time()
                return True
    return False


def _format_elapsed(seconds: float) -> str:
    mins, secs = divmod(int(seconds), 60)
    if mins:
        return f"{mins}m {secs}s"
    return f"{secs}s"


def start_async_job(
    kind: JobKind,
    label: str,
    coro_factory: Callable[[Callable[..., None]], Any],
    *,
    meta: dict[str, Any] | None = None,
) -> str | None:
    """Start coroutine in a background thread. coro_factory receives progress_callback.

    Returns job id when a new job starts, existing id if same kind already running,
    or None if another job kind is already running.
    """
    session_key = _session_key()

    if is_kind_running(kind):
        with _lock:
            for job in _jobs_for_session(session_key).values():
                if job["kind"] == kind.value and job["status"] == "running":
                    return job["id"]

    with _lock:
        for job in _jobs_for_session(session_key).values():
            if job["status"] == "running" and job["kind"] != kind.value:
                return None

    job_id = uuid.uuid4().hex[:10]
    now = time.time()
    job: dict[str, Any] = {
        "id": job_id,
        "kind": kind.value,
        "label": label,
        "status": "running",
        "progress": 0.0,
        "message": "Starting…",
        "result": None,
        "error": None,
        "meta": meta or {},
        "synced": False,
        "session_key": session_key,
        "started_at": now,
        "updated_at": now,
    }
    with _lock:
        _jobs_for_session(session_key)[job_id] = job

    def progress_callback(*args: Any) -> None:
        fields: dict[str, Any] = {}
        if len(args) == 1:
            msg = str(args[0])
            fields["message"] = msg
            with _lock:
                job = _jobs_for_session(session_key).get(job_id) or {}
                prev = float(job.get("progress", 0.02))
            # Early-phase messages (prep / backfill) — creep forward so the bar is not frozen.
            if "backfill" in msg.lower() or "prepar" in msg.lower() or "loading" in msg.lower():
                fields["progress"] = min(prev + 0.012, 0.22)
            else:
                fields["progress"] = min(prev + 0.008, 0.92)
        elif len(args) >= 5:
            _tier, _symbol, i, total, msg = args[:5]
            ratio = i / max(total, 1)
            fields["progress"] = min(0.08 + ratio * 0.84, 0.92)
            fields["message"] = str(msg)
        elif len(args) >= 4:
            current, total, message, _partial = args[:4]
            ratio = current / max(total, 1)
            fields["progress"] = min(0.05 + ratio * 0.93, 0.98)
            fields["message"] = str(message)
        elif len(args) == 3:
            current, total, message = args
            ratio = current / max(total, 1)
            fields["progress"] = min(0.05 + ratio * 0.93, 0.98)
            fields["message"] = str(message)
        if fields:
            with _lock:
                job = _jobs_for_session(session_key).get(job_id) or {}
                prev = float(job.get("progress", 0.02))
            if "progress" in fields:
                fields["progress"] = max(prev, fields["progress"])
            job_registry.update_job(job_id, session_key, **fields)

    def worker() -> None:
        async def _run_job() -> Any:
            try:
                from app.services.audit import audit_track
                from app.services.audit_types import AuditComponent
            except ImportError as audit_exc:
                import logging

                logging.getLogger("app.audit").warning(
                    "Audit unavailable for job %s, running without audit: %s",
                    kind.value,
                    audit_exc,
                )
                return await coro_factory(progress_callback)

            async with audit_track(
                f"job.{kind.value}",
                AuditComponent.JOB,
                session_id=session_key,
                job_id=job_id,
                **(meta or {}),
            ):
                return await coro_factory(progress_callback)

        try:
            job_registry.update_job(job_id, session_key, message="Running…", progress=0.02)
            # Isolated loop+engine so long jobs (sim / recommendations) never hold
            # the Streamlit UI exclusive DB lock — that was blanking the browser.
            from ui.async_runner import run_isolated_async

            result = run_isolated_async(_run_job)
            with _lock:
                current = _jobs_for_session(session_key).get(job_id)
                if current is None:
                    return
                if current.get("status") != "running":
                    return
            job_registry.update_job(
                job_id,
                session_key,
                status="completed",
                progress=1.0,
                message="Complete",
                result=result,
            )
        except Exception as exc:
            job_registry.update_job(
                job_id,
                session_key,
                status="failed",
                message="Failed",
                error=str(exc),
            )

    threading.Thread(target=worker, daemon=True, name=f"bg-{kind.value}").start()
    return job_id


def _apply_completed_job(job: dict[str, Any]) -> None:
    kind = job["kind"]
    result = job.get("result")
    meta = job.get("meta") or {}

    if kind == JobKind.SIM_BACKTEST.value:
        if result is None:
            st.session_state["sim_last_error"] = (
                "Insufficient candle data after automatic market backfill — the simulation needs "
                "**51 trading days** per stock (20-day lookback + 30-day eval). "
                "Check network/NSE access, then try **Refresh market data** on the Trading tab."
            )
            return
        report, run = result
        if report is None:
            st.session_state["sim_last_error"] = (
                "Insufficient candle data after automatic market backfill — the simulation needs "
                "**51 trading days** per stock (20-day lookback + 30-day eval). "
                "Check network/NSE access, then try **Refresh market data** on the Trading tab."
            )
            return
        universe = meta.get("universe", "")
        st.session_state["live_sim_report"] = report
        st.session_state["live_sim_universe"] = universe
        st.session_state["live_sim_from_cache"] = False
        if run is not None:
            st.session_state["live_sim_run_id"] = run.id
            st.session_state["live_sim_run_at"] = getattr(run, "run_at", None)
        st.session_state.pop("sim_last_error", None)
        st.session_state["sim_last_success"] = (
            f"Hard refresh complete — {universe} · {len(report.patterns)} patterns · "
            f"{report.stock_count} stocks"
        )

    elif kind == JobKind.TODAY_PREDICTION.value:
        if result is None:
            st.session_state["today_pred_last_error"] = (
                "Insufficient candle data — predictions need at least **21 trading days** per stock. "
                "Run **Refresh market data** on the Trading tab."
            )
            return
        st.session_state["today_prediction_report"] = result
        st.session_state.pop("today_pred_last_error", None)
        st.session_state["today_pred_last_success"] = (
            f"Scored predictions for {result.prediction_date.strftime('%d %b %Y')} "
            f"(lookback through {result.data_through_date.strftime('%d %b %Y')})"
        )

    elif kind == JobKind.RECOMMENDATIONS.value:
        report, allocation = result
        meta = job.get("meta") or {}
        st.session_state["rec_report"] = report
        st.session_state["rec_allocation"] = allocation
        st.session_state["rec_from_cache"] = False
        if meta.get("budget_inr") is not None:
            st.session_state["rec_budget"] = float(meta["budget_inr"])
        if meta.get("max_target_profit_pct") is not None:
            st.session_state["rec_max_target_pct"] = float(meta["max_target_profit_pct"])
        st.session_state.pop("rec_last_error", None)
        from app.services.recommendation_engine import all_report_recommendations

        total = len(all_report_recommendations(report))
        cap_n = len(report.recommendations)
        bucket_n = total - cap_n
        st.session_state["rec_last_success"] = (
            f"Generated {total} recommendations "
            f"({cap_n} cap tier, {bucket_n} price bucket) for "
            f"{report.prediction_date.strftime('%d %b %Y')}"
        )

    elif kind == JobKind.MIDDAY_RECOMMENDATIONS.value:
        report, allocation = result
        meta = job.get("meta") or {}
        st.session_state["midday_report"] = report
        st.session_state["midday_allocation"] = allocation
        st.session_state["midday_from_cache"] = False
        if meta.get("budget_inr") is not None:
            st.session_state["midday_budget"] = float(meta["budget_inr"])
        if meta.get("max_target_profit_pct") is not None:
            st.session_state["midday_max_target_pct"] = float(meta["max_target_profit_pct"])
        from app.services.recommendation_cache import load_midday_cached_recommendations_for_ui

        cached = load_midday_cached_recommendations_for_ui()
        if cached is not None:
            _, _, _, _, cached_at = cached
            st.session_state["midday_cached_at"] = cached_at
        st.session_state.pop("midday_last_error", None)
        from app.services.recommendation_engine import all_report_recommendations

        total = len(all_report_recommendations(report))
        st.session_state["midday_last_success"] = (
            f"Mid-day analysis complete — {total} picks for "
            f"{report.prediction_date.strftime('%d %b %Y')} "
            f"(data through {report.data_through_date.strftime('%d %b %Y')})"
        )

    elif kind == JobKind.MARKET_SYNC.value:
        if not result:
            st.session_state["market_sync_last_error"] = "Market sync returned no result."
            return
        st.session_state.pop("market_sync_last_error", None)
        st.session_state["market_sync_last_success"] = (
            f"Synced {result.get('candles_upserted', 0)} candles through "
            f"{result.get('data_through', result.get('date_end', '?'))} · "
            f"{result.get('instruments_fetched', result.get('instruments', 0))} updated · "
            f"{result.get('instruments_skipped', 0)} already current · "
            f"{result.get('allowed_symbols', result.get('equity_instruments', '?'))} in {result.get('universe', 'NIFTY250')}"
            + (
                f" · removed {result.get('instruments_deleted', 0)} delisted"
                if result.get("instruments_deleted")
                else ""
            )
            + (
                f" · trimmed {result.get('candles_trimmed', 0)} out-of-range rows"
                if result.get("candles_trimmed")
                else ""
            )
        )


def _reconcile_stale_jobs() -> None:
    """Last-resort recovery when worker finished but registry still shows running."""
    import logging

    log = logging.getLogger("ui.background_jobs")
    session_key = _session_key()
    now = time.time()
    with _lock:
        jobs = list(_jobs_for_session(session_key).values())

    for job in jobs:
        if job.get("status") != "running":
            continue
        started = float(job.get("started_at", now))
        updated = float(job.get("updated_at", started))
        age = now - updated
        if age < 30:
            continue

        kind = job.get("kind")
        if kind == JobKind.RECOMMENDATIONS.value:
            try:
                from ui.async_runner import run_async
                from app.services.recommendation_cache import load_cached_recommendations_for_ui

                cached = run_async(load_cached_recommendations_for_ui())
                if cached is None:
                    continue
                report, allocation, _budget, _max_target, cached_at = cached
                cached_ts = (
                    cached_at.timestamp()
                    if hasattr(cached_at, "timestamp")
                    else started
                )
                if cached_ts + 1 < started:
                    continue
                log.warning(
                    "Reconciling stale recommendation job %s (registry stuck running)",
                    job.get("id"),
                )
                job_registry.update_job(
                    job["id"],
                    session_key,
                    status="completed",
                    progress=1.0,
                    message="Complete",
                    result=(report, allocation),
                )
            except Exception:
                continue
        elif age > STALE_JOB_SECONDS:
            job_registry.update_job(
                job["id"],
                session_key,
                status="failed",
                message="Timed out",
                error="Background task stopped reporting progress",
            )


def sync_jobs_to_session() -> None:
    """Main thread: move finished job results into st.session_state."""
    _reconcile_stale_jobs()
    with _lock:
        jobs = list(_session_jobs().values())

    needs_rerun = False
    for job in jobs:
        if job.get("synced"):
            continue
        if job["status"] == "completed":
            _apply_completed_job(job)
            job["synced"] = True
            st.session_state["last_job_notice"] = job.get("label", "Task") + " finished."
            needs_rerun = True
        elif job["status"] == "failed":
            err = job.get("error") or "Unknown error"
            kind = job["kind"]
            if kind == JobKind.SIM_BACKTEST.value:
                st.session_state["sim_last_error"] = err
            elif kind == JobKind.TODAY_PREDICTION.value:
                st.session_state["today_pred_last_error"] = err
            elif kind == JobKind.RECOMMENDATIONS.value:
                st.session_state["rec_last_error"] = err
            elif kind == JobKind.MIDDAY_RECOMMENDATIONS.value:
                st.session_state["midday_last_error"] = err
            elif kind == JobKind.MARKET_SYNC.value:
                st.session_state["market_sync_last_error"] = err
            job["synced"] = True
            st.session_state["last_job_notice"] = f"{job.get('label', 'Task')} failed: {err}"
            needs_rerun = True

    if needs_rerun:
        st.session_state["_job_finished_rerun"] = True


def render_sidebar_job_status(slot: "st.delta_generator.DeltaGenerator") -> None:
    """Show running background tasks in the reserved sidebar slot."""
    running = list_jobs()
    if not running:
        slot.empty()
        return

    with slot.container():
        st.markdown("**Background tasks**")
        now = time.time()
        for job in running:
            elapsed = _format_elapsed(now - job.get("started_at", now))
            stale = now - job.get("updated_at", job.get("started_at", now)) > STALE_JOB_SECONDS
            caption = f"⏳ {job['label']} · {elapsed}"
            if stale:
                caption += " · no updates for a while"
            st.caption(caption)
            st.progress(job.get("progress", 0.0), text=job.get("message", "Running…"))


def poll_running_jobs(poll_seconds: float = 1.5) -> None:
    """Legacy hook — prefer ``run_background_job_watcher`` fragment in dashboard."""
    if st.session_state.pop("_job_finished_rerun", False):
        st.rerun()


def run_background_job_watcher(
    *,
    poll_seconds: float = 1.0,
    slot: "st.delta_generator.DeltaGenerator",
) -> None:
    """Lightweight fragment poll: sync job state without full-page sleep/rerun loop."""

    def _draw() -> None:
        render_sidebar_job_status(slot)

    # Initial full-app write so the fragment can update this slot on reruns.
    sync_jobs_to_session()
    _draw()

    @st.fragment(run_every=timedelta(seconds=poll_seconds))
    def _watch() -> None:
        sync_jobs_to_session()
        _draw()

    _watch()


def start_sim_backtest_job(universe: str, stock_count: int) -> str | None:
    from ui.helpers import _run_backtest

    async def coro(progress_callback):
        progress_callback("Preparing simulation…")
        return await _run_backtest(
            progress_callback=progress_callback,
            step_delay_sec=0.0,
            universe=universe,
            force_refresh=True,
        )

    return start_async_job(
        JobKind.SIM_BACKTEST,
        f"30-day simulation ({universe})",
        coro,
        meta={"universe": universe},
    )


def start_today_prediction_job(universe: str) -> str | None:
    from ui.helpers import _run_today_prediction

    async def coro(progress_callback):
        # Uses local OHLCV only (sync_first=False) — do not imply a market sync.
        progress_callback("Scoring today's predictions from local market data…")
        return await _run_today_prediction(sync_first=False, universe=universe)

    return start_async_job(
        JobKind.TODAY_PREDICTION,
        f"Today's predictions ({universe})",
        coro,
        meta={"universe": universe},
    )


def start_recommendations_job(
    budget_inr: float,
    *,
    max_target_profit_pct: float | None = None,
) -> str | None:
    from app.services.market_calendar import is_evening_recommendation_ready
    from ui.streamlit_imports import ensure_recommendation_helpers_fresh

    if not is_evening_recommendation_ready():
        return None

    ensure_recommendation_helpers_fresh()
    from ui.recommendation_helpers import run_recommendation_analysis

    async def coro(progress_callback):
        progress_callback("Starting recommendation scan…")
        return await run_recommendation_analysis(
            budget_inr,
            progress_callback=progress_callback,
            max_target_profit_pct=max_target_profit_pct,
        )

    return start_async_job(
        JobKind.RECOMMENDATIONS,
        "Recommendation analysis",
        coro,
        meta={"budget_inr": budget_inr, "max_target_profit_pct": max_target_profit_pct},
    )


def start_midday_recommendations_job(
    budget_inr: float,
    *,
    max_target_profit_pct: float | None = None,
) -> str | None:
    from ui.streamlit_imports import ensure_recommendation_helpers_fresh

    ensure_recommendation_helpers_fresh()
    from ui.recommendation_helpers import run_midday_recommendation_analysis

    async def coro(progress_callback):
        progress_callback("Starting mid-day recommendation scan…")
        return await run_midday_recommendation_analysis(
            budget_inr,
            progress_callback=progress_callback,
            max_target_profit_pct=max_target_profit_pct,
        )

    return start_async_job(
        JobKind.MIDDAY_RECOMMENDATIONS,
        "Mid-day recommendation analysis",
        coro,
        meta={"budget_inr": budget_inr, "max_target_profit_pct": max_target_profit_pct},
    )


def start_market_sync_job(*, force: bool = True) -> str | None:
    """Start market sync. ``force=True`` (manual refresh) always runs; auto callers may pass False."""
    from app.services.ingestion import sync_latest
    from app.services.market_sync_status import daily_auto_sync_needed, record_market_sync_success
    from ui.async_runner import run_async

    if not force:
        needed = run_async(lambda: daily_auto_sync_needed(force=False), timeout=30, retries=0)
        if not needed:
            return None

    async def coro(progress_callback):
        progress_callback("Preparing NIFTY250 market sync…")

        def _progress(current, total, message):
            progress_callback(current, total, message, None)

        result = await sync_latest(progress_callback=_progress)
        record_market_sync_success(result.get("data_through"))
        return result

    return start_async_job(
        JobKind.MARKET_SYNC,
        "Market data sync (NIFTY250)",
        coro,
    )

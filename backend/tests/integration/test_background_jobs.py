"""Background job manager tests."""

from __future__ import annotations

import asyncio
import time

import pytest

from ui.background_jobs import JobKind, is_kind_running, list_jobs, start_async_job


@pytest.mark.quick
def test_background_job_completes() -> None:
    async def fast_coro(progress_callback):
        progress_callback("step 1")
        await asyncio.sleep(0.05)
        return {"ok": True}

    job_id = start_async_job(JobKind.TODAY_PREDICTION, "test job", fast_coro)
    assert job_id

    deadline = time.time() + 5
    while time.time() < deadline:
        if not is_kind_running(JobKind.TODAY_PREDICTION):
            break
        time.sleep(0.05)

    assert not is_kind_running(JobKind.TODAY_PREDICTION)
    jobs = list_jobs(include_completed=True)
    finished = next(j for j in jobs if j["id"] == job_id)
    assert finished["status"] == "completed"
    assert finished["result"] == {"ok": True}
    assert finished["message"] != "Starting…"


@pytest.mark.quick
def test_background_job_progress_visible_from_worker_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """Progress updates must use the main-thread session key, not 'default'."""
    import ui.background_jobs as bg

    monkeypatch.setattr(bg, "_session_key", lambda: "browser-session-1")

    seen: list[str] = []

    async def slow_coro(progress_callback):
        progress_callback(1, 10, "loading RELIANCE", None)
        await asyncio.sleep(0.1)
        return True

    job_id = bg.start_async_job(JobKind.SIM_BACKTEST, "sim", slow_coro)
    deadline = time.time() + 5
    while time.time() < deadline:
        with bg._lock:
            job = bg._jobs_for_session("browser-session-1").get(job_id)
        if job and "RELIANCE" in (job.get("message") or ""):
            seen.append(job["message"])
            break
        time.sleep(0.05)

    assert seen, "progress message never reached the UI session bucket"
    assert "RELIANCE" in seen[0]

    deadline = time.time() + 5
    while time.time() < deadline:
        if not bg.is_kind_running(bg.JobKind.SIM_BACKTEST):
            break
        time.sleep(0.05)


@pytest.mark.quick
def test_background_job_three_arg_progress_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mid-day OHLC sync uses progress_callback(current, total, message)."""
    import ui.background_jobs as bg

    monkeypatch.setattr(bg, "_session_key", lambda: "browser-session-2")

    seen: list[tuple[float, str]] = []

    async def slow_coro(progress_callback):
        progress_callback(125, 250, "Session OHLC · RELIANCE (125/250)")
        await asyncio.sleep(0.15)
        return True

    job_id = bg.start_async_job(JobKind.MIDDAY_RECOMMENDATIONS, "midday", slow_coro)
    deadline = time.time() + 5
    while time.time() < deadline:
        with bg._lock:
            job = bg._jobs_for_session("browser-session-2").get(job_id)
        if job and "RELIANCE" in (job.get("message") or ""):
            seen.append((float(job.get("progress", 0)), job["message"]))
            break
        time.sleep(0.02)

    assert seen, "3-arg progress callback did not update job message"
    progress, message = seen[0]
    assert progress > 0.4
    assert "125/250" in message

    deadline = time.time() + 5
    while time.time() < deadline:
        if not bg.is_kind_running(JobKind.MIDDAY_RECOMMENDATIONS):
            break
        time.sleep(0.05)


@pytest.mark.quick
def test_session_key_migrates_job_bucket() -> None:
    """Jobs started under one session id remain visible after Streamlit ctx changes."""
    from ui import job_registry

    with job_registry._lock:
        job_registry._jobs_by_session.clear()

    old_key = "session-old"
    new_key = "session-new"
    job_registry.jobs_for_session(old_key)["j1"] = {
        "id": "j1",
        "kind": JobKind.RECOMMENDATIONS.value,
        "status": "running",
        "session_key": old_key,
    }

    job_registry.migrate_session_jobs(old_key, new_key)

    with job_registry._lock:
        assert "j1" in job_registry._jobs_by_session.get(new_key, {})
        assert old_key not in job_registry._jobs_by_session


@pytest.mark.quick
def test_orphan_default_jobs_adopted_by_real_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Jobs registered under 'default' before ctx appears attach to the browser session."""
    from ui import job_registry
    import ui.background_jobs as bg

    with job_registry._lock:
        job_registry._jobs_by_session.clear()

    job_registry.jobs_for_session("default")["orphan"] = {
        "id": "orphan",
        "kind": JobKind.RECOMMENDATIONS.value,
        "status": "running",
        "session_key": "default",
    }

    class _Ctx:
        session_id = "browser-abc"

    monkeypatch.setattr(
        "streamlit.runtime.scriptrunner.get_script_run_ctx",
        lambda: _Ctx(),
    )
    monkeypatch.setitem(bg.st.session_state, "_job_session_key", "browser-abc")

    jobs = bg._session_jobs()
    assert "orphan" in jobs
    assert jobs["orphan"]["session_key"] == "browser-abc"

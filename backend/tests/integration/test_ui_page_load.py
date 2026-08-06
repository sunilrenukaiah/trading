"""UI page-load tests — simulate Streamlit navigation across all sidebar pages."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from ui.ui_load_harness import (
    NAV_PAGES,
    PAGE_RENDERERS,
    assert_all_pages_loaded,
    expected_title_for,
    load_all_nav_pages,
    run_initial_load,
)


@pytest.mark.quick
def test_ui_load_harness_nav_pages_match_dashboard() -> None:
    """Harness page list must match dashboard Navigate radio (no dropped tabs)."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "ui" / "dashboard.py").read_text(encoding="utf-8")
    for page in NAV_PAGES:
        assert f'"{page}"' in src, f"Missing nav page in dashboard.py: {page}"


@pytest.mark.quick
def test_ui_load_harness_renderers_exist() -> None:
    import ui.dashboard as dashboard

    for name in PAGE_RENDERERS:
        assert hasattr(dashboard, name), f"ui.dashboard missing {name}"
        assert callable(getattr(dashboard, name))


@pytest.mark.quick
def test_isolated_job_does_not_block_ui_run_async() -> None:
    """Regression: background work must not hold the UI exclusive DB lock."""
    from ui.async_runner import run_async, run_isolated_async

    started = threading.Event()
    release = threading.Event()

    async def _long_job():
        started.set()
        # Simulate CPU / IO while UI should still get the exclusive lock.
        for _ in range(40):
            if release.is_set():
                break
            await asyncio.sleep(0.05)
        return "job-done"

    errors: list[BaseException] = []
    job_holder: dict[str, object] = {}

    def _job_thread() -> None:
        try:
            job_holder["result"] = run_isolated_async(_long_job, timeout=10)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=_job_thread, daemon=True)
    t.start()
    assert started.wait(timeout=5), "isolated job did not start"

    async def _ui_ping():
        await asyncio.sleep(0)
        return "ui-ok"

    t0 = time.perf_counter()
    ui_result = run_async(_ui_ping, timeout=5, retries=0)
    elapsed = time.perf_counter() - t0
    release.set()
    t.join(timeout=10)

    assert ui_result == "ui-ok"
    assert elapsed < 2.0, f"UI run_async blocked for {elapsed:.2f}s while isolated job ran"
    assert not errors, f"isolated job errors: {errors!r}"
    assert job_holder.get("result") == "job-done"


@pytest.mark.db
@pytest.mark.post_deploy
def test_streamlit_initial_page_loads() -> None:
    """Default Trading page must paint without exception (requires Postgres)."""
    result = run_initial_load(timeout=120.0)
    assert result.ok, f"initial load failed: {result}"
    assert result.element_count > 0
    if result.title:
        assert expected_title_for("Trading") in result.title


@pytest.mark.db
@pytest.mark.post_deploy
def test_streamlit_all_nav_pages_load() -> None:
    """Every sidebar page must render content — catches blank-shell regressions."""
    results = load_all_nav_pages(timeout_per_page=180.0)
    assert len(results) == len(NAV_PAGES)
    assert_all_pages_loaded(results)

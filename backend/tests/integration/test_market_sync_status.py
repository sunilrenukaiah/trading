"""Tests for durable daily market-sync skip logic."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

IST = ZoneInfo("Asia/Kolkata")


@pytest.mark.quick
def test_record_and_detect_daily_auto_sync_done(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import market_sync_status as mss

    monkeypatch.setattr(mss, "STATUS_PATH", tmp_path / "market_sync_status.json")

    now = datetime(2026, 8, 6, 16, 5, tzinfo=IST)
    mss.record_market_sync_success(date(2026, 8, 6), now=now)

    assert mss.daily_auto_sync_already_done(now=now) is True
    # Next morning still "done" for same calendar evening check fails on date mismatch
    next_morning = datetime(2026, 8, 7, 10, 0, tzinfo=IST)
    assert mss.daily_auto_sync_already_done(now=next_morning) is False


@pytest.mark.quick
def test_auto_sync_needed_false_after_recorded_sync(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import market_sync_status as mss

    monkeypatch.setattr(mss, "STATUS_PATH", tmp_path / "market_sync_status.json")
    now = datetime(2026, 8, 6, 18, 5, tzinfo=IST)
    mss.record_market_sync_success(date(2026, 8, 6), now=datetime(2026, 8, 6, 15, 50, tzinfo=IST))

    import asyncio

    assert asyncio.get_event_loop().run_until_complete(mss.daily_auto_sync_needed(force=False, now=now)) is False
    assert asyncio.get_event_loop().run_until_complete(mss.daily_auto_sync_needed(force=True, now=now)) is True


@pytest.mark.quick
def test_today_prediction_job_message_not_sync() -> None:
    import inspect

    from ui import background_jobs as bg

    src = inspect.getsource(bg.start_today_prediction_job)
    assert "Syncing market data" not in src
    assert "local market data" in src


@pytest.mark.quick
def test_scheduled_sync_skips_when_daily_done(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import market_sync_status as mss
    from ui import scheduled_market_sync as sms

    monkeypatch.setattr(mss, "STATUS_PATH", tmp_path / "market_sync_status.json")
    now = datetime(2026, 8, 6, 18, 10, tzinfo=IST)
    mss.record_market_sync_success(date(2026, 8, 6), now=datetime(2026, 8, 6, 16, 1, tzinfo=IST))

    # Fake streamlit session state
    class _SS(dict):
        def setdefault(self, k, default=None):
            if k not in self:
                self[k] = default if default is not None else {}
            return self[k]

    import streamlit as st

    monkeypatch.setattr(st, "session_state", _SS())
    monkeypatch.setattr(sms, "due_scheduled_sync_slot", lambda now=None: "18:00")
    monkeypatch.setattr("ui.background_jobs.is_any_job_running", lambda: False)

    started = {"count": 0}

    def _fake_start(*, force=True):
        started["count"] += 1
        return "job-1"

    monkeypatch.setattr("ui.background_jobs.start_market_sync_job", _fake_start)
    monkeypatch.setattr(
        "ui.async_runner.run_async",
        lambda factory, **kwargs: __import__("asyncio").get_event_loop().run_until_complete(factory()),
    )

    result = sms.try_start_scheduled_market_sync()
    assert result is None
    assert started["count"] == 0

"""Tests for persisted bracket reconcile timestamps."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.services import bracket_reconcile_state as state_mod

IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture
def state_path(tmp_path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "data" / "bracket_reconcile_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(state_mod, "STATE_PATH", path)
    return path


@pytest.mark.quick
def test_should_auto_reconcile_when_never_run(state_path) -> None:
    assert state_mod.should_auto_reconcile(
        now=datetime(2026, 8, 5, 11, 0, tzinfo=IST),
        session_date=date(2026, 8, 5),
    )


@pytest.mark.quick
def test_should_auto_reconcile_when_session_day_changes(state_path) -> None:
    state_mod.record_reconcile_success(
        now=datetime(2026, 8, 4, 15, 30, tzinfo=IST),
        session_date=date(2026, 8, 4),
    )
    assert state_mod.should_auto_reconcile(
        now=datetime(2026, 8, 5, 9, 30, tzinfo=IST),
        session_date=date(2026, 8, 5),
    )


@pytest.mark.quick
def test_should_auto_reconcile_when_stale(state_path) -> None:
    state_mod.record_reconcile_success(
        now=datetime(2026, 8, 5, 10, 0, tzinfo=IST),
        session_date=date(2026, 8, 5),
    )
    assert not state_mod.should_auto_reconcile(
        now=datetime(2026, 8, 5, 10, 4, tzinfo=IST),
        session_date=date(2026, 8, 5),
        stale_minutes=5,
    )
    assert state_mod.should_auto_reconcile(
        now=datetime(2026, 8, 5, 10, 6, tzinfo=IST),
        session_date=date(2026, 8, 5),
        stale_minutes=5,
    )


@pytest.mark.quick
def test_record_live_poll_persists(state_path) -> None:
    when = datetime(2026, 8, 5, 11, 15, tzinfo=IST)
    state_mod.record_live_poll(now=when)
    loaded = state_mod.load_bracket_reconcile_state()
    assert loaded.last_live_poll_at == when


@pytest.mark.quick
def test_format_reconcile_notice() -> None:
    notice = state_mod.format_reconcile_notice(
        {"targets": 2, "entries": 0, "stops": 1},
        prefix="Catch-up",
    )
    assert notice == "Catch-up: 2 targets hit, 1 stops hit"


@pytest.mark.quick
def test_format_reconcile_notice_empty() -> None:
    assert state_mod.format_reconcile_notice({}) is None

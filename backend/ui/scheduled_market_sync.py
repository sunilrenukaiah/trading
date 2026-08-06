"""Automatic market data sync at fixed IST times (3:45 PM and 6:00 PM).

Skips when today's post-session sync already completed (durable status / DB).
Manual Refresh market data always forces a sync.
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import streamlit as st

from app.services.market_calendar import IST, is_trading_day

# Post-session provisional sync + final EOD refresh
SCHEDULED_SYNC_SLOTS: tuple[tuple[int, int], ...] = ((15, 45), (18, 0))
_SESSION_KEY = "scheduled_market_sync_done"


def _slot_key(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


def due_scheduled_sync_slot(*, now: datetime | None = None) -> str | None:
    """Return the next due sync slot key (e.g. '15:45') or None."""
    current = now or datetime.now(IST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=IST)
    else:
        current = current.astimezone(IST)

    if not is_trading_day(current.date()):
        return None

    done = st.session_state.setdefault(_SESSION_KEY, {})
    day_key = current.date().isoformat()
    done_today = set(done.get(day_key, []))
    now_t = current.time()

    for hour, minute in SCHEDULED_SYNC_SLOTS:
        key = _slot_key(hour, minute)
        if now_t >= time(hour, minute) and key not in done_today:
            return key
    return None


def mark_scheduled_sync_started(slot_key: str, *, now: datetime | None = None) -> None:
    current = now or datetime.now(IST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=IST)
    else:
        current = current.astimezone(IST)

    done = st.session_state.setdefault(_SESSION_KEY, {})
    day_key = current.date().isoformat()
    slots = set(done.get(day_key, []))
    slots.add(slot_key)
    done[day_key] = sorted(slots)
    st.session_state[_SESSION_KEY] = done


def try_start_scheduled_market_sync() -> str | None:
    """
    Start a background market sync job if a scheduled slot is due.

    Returns the slot key when a job was started, else None.
    If today's data was already synced after the post-session window, marks the
    slot done and skips the network sync.
    """
    from ui.async_runner import run_async
    from ui.background_jobs import is_any_job_running, start_market_sync_job
    from ui.helpers import _ui_scheduled_jobs_disabled

    if _ui_scheduled_jobs_disabled():
        return None

    slot = due_scheduled_sync_slot()
    if slot is None or is_any_job_running():
        return None

    from app.services.market_sync_status import daily_auto_sync_needed

    needed = run_async(lambda: daily_auto_sync_needed(force=False), timeout=30, retries=0)
    if not needed:
        # Already have today's post-session data — don't sync again.
        mark_scheduled_sync_started(slot)
        return None

    job_id = start_market_sync_job(force=True)
    if job_id:
        mark_scheduled_sync_started(slot)
        return slot
    return None

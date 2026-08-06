"""Persist bracket reconcile / live-poll timestamps across UI restarts."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.defaults import DEFAULT_BRACKET_RECONCILE_STALE_MINUTES

IST = ZoneInfo("Asia/Kolkata")
STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "bracket_reconcile_state.json"


@dataclass
class BracketReconcileState:
    last_reconcile_at: datetime | None = None
    last_reconcile_session_date: date | None = None
    last_live_poll_at: datetime | None = None


def load_bracket_reconcile_state() -> BracketReconcileState:
    if not STATE_PATH.is_file():
        return BracketReconcileState()
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return BracketReconcileState()

    def _parse_dt(key: str) -> datetime | None:
        value = raw.get(key)
        if not value:
            return None
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=IST)
        return dt.astimezone(IST)

    session_raw = raw.get("last_reconcile_session_date")
    session_date = date.fromisoformat(session_raw) if session_raw else None
    return BracketReconcileState(
        last_reconcile_at=_parse_dt("last_reconcile_at"),
        last_reconcile_session_date=session_date,
        last_live_poll_at=_parse_dt("last_live_poll_at"),
    )


def save_bracket_reconcile_state(state: BracketReconcileState) -> None:
    payload = {
        "last_reconcile_at": (
            state.last_reconcile_at.astimezone(IST).isoformat()
            if state.last_reconcile_at
            else None
        ),
        "last_reconcile_session_date": (
            state.last_reconcile_session_date.isoformat()
            if state.last_reconcile_session_date
            else None
        ),
        "last_live_poll_at": (
            state.last_live_poll_at.astimezone(IST).isoformat()
            if state.last_live_poll_at
            else None
        ),
    }
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def record_reconcile_success(
    *,
    now: datetime | None = None,
    session_date: date | None = None,
) -> BracketReconcileState:
    current = now.astimezone(IST) if now is not None else datetime.now(IST)
    state = load_bracket_reconcile_state()
    state.last_reconcile_at = current
    if session_date is not None:
        state.last_reconcile_session_date = session_date
    save_bracket_reconcile_state(state)
    return state


def record_live_poll(*, now: datetime | None = None) -> BracketReconcileState:
    current = now.astimezone(IST) if now is not None else datetime.now(IST)
    state = load_bracket_reconcile_state()
    state.last_live_poll_at = current
    save_bracket_reconcile_state(state)
    return state


def should_auto_reconcile(
    *,
    now: datetime | None = None,
    session_date: date | None = None,
    stale_minutes: int = DEFAULT_BRACKET_RECONCILE_STALE_MINUTES,
) -> bool:
    """Run bracket catch-up when never reconciled, session rolled, or last run is stale."""
    from app.services.market_calendar import current_session_date

    current = now.astimezone(IST) if now is not None else datetime.now(IST)
    active_session = session_date or current_session_date(now=current)
    state = load_bracket_reconcile_state()
    if state.last_reconcile_at is None:
        return True
    if state.last_reconcile_session_date != active_session:
        return True
    age = current - state.last_reconcile_at.astimezone(IST)
    return age >= timedelta(minutes=stale_minutes)


def format_reconcile_notice(
    stats: dict[str, object],
    *,
    prefix: str = "Bracket reconcile",
) -> str | None:
    parts: list[str] = []
    for key, label in (
        ("entries", "entries filled"),
        ("targets", "targets hit"),
        ("stops", "stops hit"),
        ("square_offs", "square-offs"),
        ("cancelled_pending", "pending cancelled"),
    ):
        count = int(stats.get(key, 0) or 0)
        if count:
            parts.append(f"{count} {label}")
    if not parts:
        return None
    return f"{prefix}: " + ", ".join(parts)


def state_as_dict(state: BracketReconcileState) -> dict[str, str | None]:
    return {
        key: (value.isoformat() if value is not None else None)
        for key, value in asdict(state).items()
    }

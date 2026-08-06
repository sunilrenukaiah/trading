"""Process-wide background job registry — never reload this module at runtime.

Streamlit reruns reload ``ui.background_jobs``; worker threads must update
state here so the UI always sees completion/progress.
"""

from __future__ import annotations

import threading
import time
from typing import Any

_lock = threading.RLock()
_jobs_by_session: dict[str, dict[str, dict[str, Any]]] = {}


def jobs_for_session(session_key: str) -> dict[str, dict[str, Any]]:
    with _lock:
        return _jobs_by_session.setdefault(session_key, {})


def update_job(job_id: str, session_key: str, **fields: Any) -> None:
    fields.setdefault("updated_at", time.time())
    with _lock:
        jobs = jobs_for_session(session_key)
        if job_id in jobs:
            jobs[job_id].update(fields)


def migrate_session_jobs(old_key: str, new_key: str) -> None:
    """Move jobs when Streamlit session id changes (reconnect / ctx loss)."""
    if not old_key or old_key == new_key:
        return
    with _lock:
        old_jobs = _jobs_by_session.pop(old_key, None)
        if not old_jobs:
            return
        _jobs_by_session.setdefault(new_key, {}).update(old_jobs)


def all_session_jobs() -> dict[str, dict[str, dict[str, Any]]]:
    with _lock:
        return _jobs_by_session

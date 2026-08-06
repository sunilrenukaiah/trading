"""Shared pytest fixtures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session", autouse=True)
def _test_env() -> None:
    """Use a deterministic test database URL when not set."""
    os.environ.setdefault(
        "DATABASE_URL",
        "postgresql+asyncpg://trading:trading@localhost:5432/trading",
    )


@pytest.fixture(autouse=True)
def _noop_audit_during_tests() -> None:
    """Prevent integration tests from writing FAILED rows to audit_logs."""
    from app.services.audit_backends.noop import NoOpAuditWriter
    from app.services.audit_backends.registry import reset_audit_writer, set_audit_writer

    set_audit_writer(NoOpAuditWriter())
    yield
    reset_audit_writer()


@pytest.fixture(autouse=True)
def _reset_streamlit_async_runner() -> None:
    """Isolate async runner state between tests."""
    yield
    try:
        from ui.async_runner import reset_for_tests

        reset_for_tests()
    except Exception:
        pass
    try:
        from ui import job_registry

        with job_registry._lock:
            job_registry._jobs_by_session.clear()
    except Exception:
        pass

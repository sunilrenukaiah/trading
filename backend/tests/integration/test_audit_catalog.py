"""Production audit action catalog and correlation-id contract tests."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from app.services.audit_types import AuditComponent, AuditStatus
from ui.background_jobs import JobKind

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent

# Fixed action strings used in production code — renames require updating this set.
PRODUCTION_AUDIT_ACTIONS: frozenset[str] = frozenset(
    {
        "ingestion.sync_latest",
        "backtest.api_run",
        "backtest.run",
        "prediction.validate_today",
        "recommendation.run",
        "recommendation.midday_run",
        "recommendation.midday_place",
        "ui.page_render",
        "ui.tab_switch",
        "sys.unhandled_exception",
        "asyncio.unhandled_exception",
    }
)

PRODUCTION_JOB_AUDIT_ACTIONS: frozenset[str] = frozenset(
    {f"job.{kind.value}" for kind in JobKind}
)


@pytest.mark.quick
def test_production_audit_actions_present_in_source() -> None:
    """Regression guard: audit action renames must update this test."""
    source_checks = {
        "ingestion.sync_latest": BACKEND_ROOT / "app/services/ingestion.py",
        "backtest.api_run": BACKEND_ROOT / "app/api/routes/backtest.py",
        "backtest.run": BACKEND_ROOT / "ui/helpers.py",
        "prediction.validate_today": BACKEND_ROOT / "ui/helpers.py",
        "recommendation.run": BACKEND_ROOT / "ui/recommendation_helpers.py",
        "recommendation.midday_run": BACKEND_ROOT / "ui/recommendation_helpers.py",
        "recommendation.midday_place": BACKEND_ROOT / "ui/helpers.py",
        "ui.page_render": BACKEND_ROOT / "ui/tab_switch_audit.py",
        "ui.tab_switch": BACKEND_ROOT / "ui/tab_switch_audit.py",
        "sys.unhandled_exception": BACKEND_ROOT / "app/services/audit_handlers.py",
        "asyncio.unhandled_exception": BACKEND_ROOT / "app/services/audit_handlers.py",
    }
    for action, path in source_checks.items():
        text = path.read_text(encoding="utf-8")
        assert action in text, f"Expected action {action!r} in {path.relative_to(BACKEND_ROOT)}"


@pytest.mark.quick
def test_job_kind_audit_actions_match_background_jobs() -> None:
    text = (BACKEND_ROOT / "ui/background_jobs.py").read_text(encoding="utf-8")
    assert 'f"job.{kind.value}"' in text
    for kind in JobKind:
        assert kind.value in text


@pytest.mark.quick
@pytest.mark.asyncio
async def test_audit_track_shares_correlation_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import audit as audit_mod

    captured: list[tuple[AuditStatus, str | None]] = []

    def fake_schedule(action, component, status, **kwargs):
        captured.append((status, kwargs.get("correlation_id")))

    monkeypatch.setattr(audit_mod, "schedule_audit_event", fake_schedule)

    async with audit_mod.audit_track("test.correlation", AuditComponent.SERVICE):
        pass

    assert len(captured) == 2
    started_cid = captured[0][1]
    success_cid = captured[1][1]
    assert captured[0][0] == AuditStatus.STARTED
    assert captured[1][0] == AuditStatus.SUCCESS
    assert started_cid is not None
    assert started_cid == success_cid


@pytest.mark.quick
def test_audit_track_sync_shares_correlation_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import audit as audit_mod

    captured: list[str | None] = []

    def fake_schedule(**kwargs):
        captured.append(kwargs.get("correlation_id"))

    monkeypatch.setattr(audit_mod, "schedule_audit_event", fake_schedule)

    with audit_mod.audit_track_sync("test.sync.correlation", AuditComponent.SERVICE):
        pass

    assert len(captured) == 2
    assert captured[0] is not None
    assert captured[0] == captured[1]


@pytest.mark.quick
def test_audit_log_model_has_expected_columns() -> None:
    from sqlalchemy import inspect

    from app.models.audit_log import AuditLog

    columns = {c.key for c in inspect(AuditLog).columns}
    expected = {
        "id",
        "action",
        "component",
        "status",
        "duration_ms",
        "message",
        "error_type",
        "error_message",
        "traceback",
        "context",
        "session_id",
        "request_id",
        "correlation_id",
        "created_at",
    }
    assert expected <= columns


@pytest.mark.quick
@pytest.mark.asyncio
async def test_postgres_audit_writer_maps_all_event_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PostgresAuditWriter row kwargs cover every AuditLog column (except id/created_at)."""
    from app.services.audit_backends.base import AuditEvent
    from app.services.audit_backends.postgres import PostgresAuditWriter

    added_rows: list[object] = []

    class FakeSession:
        async def commit(self) -> None:
            pass

        async def refresh(self, row) -> None:
            row.id = 1

    @asynccontextmanager
    async def fake_db_session():
        session = FakeSession()
        session.add = lambda row: added_rows.append(row)
        yield session

    monkeypatch.setattr(
        "app.services.audit_backends.postgres.db_session",
        fake_db_session,
    )

    writer = PostgresAuditWriter()
    event = AuditEvent.from_kwargs(
        "test.mapping",
        AuditComponent.SERVICE.value,
        AuditStatus.SUCCESS,
        duration_ms=12,
        message="ok",
        context={"foo": "bar"},
        session_id="sess-1",
        request_id="req-1",
        correlation_id="corr-1",
    )
    row_id = await writer.write(event)
    assert row_id == 1
    assert len(added_rows) == 1
    row = added_rows[0]
    assert row.action == "test.mapping"
    assert row.component == AuditComponent.SERVICE.value
    assert row.status == AuditStatus.SUCCESS.value
    assert row.duration_ms == 12
    assert row.message == "ok"
    assert row.context == {"foo": "bar"}
    assert row.session_id == "sess-1"
    assert row.request_id == "req-1"
    assert row.correlation_id == "corr-1"

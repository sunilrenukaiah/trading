"""Audit error capture, middleware, hooks, and persistence resilience tests."""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.audit import AuditMiddleware
from app.services.audit_backends.base import AuditEvent
from app.services.audit_backends.serializers import extract_error_fields
from app.services.audit_types import AuditComponent, AuditStatus


class RecordingWriter:
    """Captures audit events in tests."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def write(self, event: AuditEvent) -> int | None:
        self.events.append(event)
        return len(self.events)


@pytest.fixture
def audit_writer(monkeypatch: pytest.MonkeyPatch) -> RecordingWriter:
    from app.config import settings
    from app.services.audit_backends.registry import reset_audit_writer, set_audit_writer

    writer = RecordingWriter()
    set_audit_writer(writer)
    monkeypatch.setattr(settings, "audit_enabled", True)
    monkeypatch.setattr(settings, "audit_blocking", True)
    yield writer
    reset_audit_writer()


@pytest.fixture
def recorded_events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    def capture(action: str, component: str, status: AuditStatus, **kwargs: Any) -> None:
        events.append({"action": action, "component": component, "status": status, **kwargs})

    monkeypatch.setattr("app.middleware.audit.schedule_audit_event", capture)
    monkeypatch.setattr("app.services.audit_dispatch.schedule_audit_event", capture)
    return events


def _audit_test_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(AuditMiddleware)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/test/ok")
    async def ok() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/test/boom")
    async def boom() -> None:
        raise RuntimeError("middleware boom")

    return app


@pytest.mark.quick
def test_audit_middleware_skips_health(recorded_events: list[dict[str, Any]]) -> None:
    app = _audit_test_app()
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert recorded_events == []


@pytest.mark.quick
def test_audit_middleware_records_success(recorded_events: list[dict[str, Any]]) -> None:
    app = _audit_test_app()
    with TestClient(app) as client:
        response = client.get("/api/test/ok")

    assert response.status_code == 200
    assert response.headers.get("X-Request-Id")
    assert len(recorded_events) == 1
    event = recorded_events[0]
    assert event["action"] == "api.get.api.test.ok"
    assert event["component"] == AuditComponent.API.value
    assert event["status"] == AuditStatus.SUCCESS
    assert event["context"]["status_code"] == 200


@pytest.mark.quick
def test_audit_middleware_records_not_found(recorded_events: list[dict[str, Any]]) -> None:
    app = _audit_test_app()
    with TestClient(app) as client:
        response = client.get("/api/test/missing")

    assert response.status_code == 404
    assert len(recorded_events) == 1
    assert recorded_events[0]["status"] == AuditStatus.CLIENT_ERROR


@pytest.mark.quick
def test_audit_middleware_records_server_error(recorded_events: list[dict[str, Any]]) -> None:
    app = _audit_test_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/test/boom")

    assert response.status_code == 500
    assert len(recorded_events) == 1
    event = recorded_events[0]
    assert event["status"] == AuditStatus.FAILED


@pytest.mark.quick
@pytest.mark.asyncio
async def test_audit_middleware_records_middleware_exception(
    recorded_events: list[dict[str, Any]],
) -> None:
    from starlette.requests import Request
    from starlette.responses import Response

    middleware = AuditMiddleware(app=MagicMock())

    async def boom(_request: Request) -> Response:
        raise RuntimeError("middleware boom")

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/test/boom",
        "headers": [],
        "query_string": b"",
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    request = Request(scope)

    with pytest.raises(RuntimeError, match="middleware boom"):
        await middleware.dispatch(request, boom)

    assert len(recorded_events) == 1
    assert recorded_events[0]["message"] == "Unhandled API exception"
    assert recorded_events[0]["status"] == AuditStatus.FAILED


@pytest.mark.quick
def test_audit_middleware_respects_request_id_header(recorded_events: list[dict[str, Any]]) -> None:
    app = _audit_test_app()
    with TestClient(app) as client:
        response = client.get("/api/test/ok", headers={"X-Request-Id": "req-abc"})

    assert response.headers["X-Request-Id"] == "req-abc"
    assert recorded_events[0]["request_id"] == "req-abc"


@pytest.mark.quick
def test_audit_logging_handler_skips_warning(recorded_events: list[dict[str, Any]]) -> None:
    from app.services.audit_handlers import AuditLoggingHandler

    handler = AuditLoggingHandler()
    record = logging.LogRecord(
        name="app.ingestion",
        level=logging.WARNING,
        pathname=__file__,
        lineno=10,
        msg="recoverable",
        args=(),
        exc_info=None,
    )
    handler.emit(record)

    assert recorded_events == []


@pytest.mark.quick
def test_audit_logging_handler_skips_audit_logger(recorded_events: list[dict[str, Any]]) -> None:
    from app.services.audit_handlers import AuditLoggingHandler

    handler = AuditLoggingHandler()
    record = logging.LogRecord(
        name="app.audit.dispatch",
        level=logging.ERROR,
        pathname=__file__,
        lineno=10,
        msg="audit internal failure",
        args=(),
        exc_info=None,
    )
    handler.emit(record)

    assert recorded_events == []


@pytest.mark.quick
def test_audit_logging_handler_captures_exc_info(recorded_events: list[dict[str, Any]]) -> None:
    from app.services.audit_handlers import AuditLoggingHandler

    handler = AuditLoggingHandler()
    try:
        raise ValueError("logged failure")
    except ValueError:
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="app.services.ingestion",
        level=logging.ERROR,
        pathname=__file__,
        lineno=10,
        msg="sync failed",
        args=(),
        exc_info=exc_info,
    )
    handler.emit(record)

    assert len(recorded_events) == 1
    assert recorded_events[0]["action"] == "log.app.services.ingestion"
    assert isinstance(recorded_events[0]["error"], ValueError)


@pytest.mark.quick
def test_sys_excepthook_records_unhandled(recorded_events: list[dict[str, Any]]) -> None:
    from app.services import audit_handlers as handlers

    handlers.reset_audit_hooks_for_tests()
    handlers.install_audit_hooks()

    try:
        raise RuntimeError("thread crash")
    except RuntimeError:
        exc_info = sys.exc_info()
        handlers._sys_excepthook(*exc_info)

    assert any(e["action"] == "sys.unhandled_exception" for e in recorded_events)
    handlers.reset_audit_hooks_for_tests()


@pytest.mark.quick
def test_asyncio_exception_handler_records_task_failure(
    recorded_events: list[dict[str, Any]],
) -> None:
    from app.services import audit_handlers as handlers

    handlers.reset_audit_hooks_for_tests()
    loop = asyncio.new_event_loop()
    handlers.install_audit_hooks(loop)

    handlers._asyncio_exception_handler(
        loop,
        {
            "message": "Task exception was never retrieved",
            "exception": ValueError("async task failed"),
            "task": "<Task>",
        },
    )

    assert any(e["action"] == "asyncio.unhandled_exception" for e in recorded_events)
    loop.close()
    handlers.reset_audit_hooks_for_tests()


@pytest.mark.quick
def test_install_audit_hooks_disabled_when_audit_off(
    monkeypatch: pytest.MonkeyPatch,
    recorded_events: list[dict[str, Any]],
) -> None:
    from app.config import settings
    from app.services import audit_handlers as handlers

    handlers.reset_audit_hooks_for_tests()
    monkeypatch.setattr(settings, "audit_enabled", False)
    handlers.install_audit_hooks()

    root = logging.getLogger()
    assert not any(isinstance(h, handlers.AuditLoggingHandler) for h in root.handlers)
    assert sys.excepthook is handlers._ORIGINAL_EXCEPTHOOK

    handlers.reset_audit_hooks_for_tests()


@pytest.mark.quick
def test_audit_track_sync_success_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import audit as audit_mod

    recorded: list[AuditStatus] = []

    def fake_schedule(**kwargs: Any) -> None:
        recorded.append(kwargs["status"])

    monkeypatch.setattr(audit_mod, "schedule_audit_event", fake_schedule)

    with audit_mod.audit_track_sync("sync.ok", AuditComponent.SERVICE):
        pass

    with pytest.raises(ValueError, match="sync boom"):
        with audit_mod.audit_track_sync("sync.fail", AuditComponent.SERVICE):
            raise ValueError("sync boom")

    assert AuditStatus.STARTED in recorded
    assert AuditStatus.SUCCESS in recorded
    assert AuditStatus.FAILED in recorded


@pytest.mark.quick
@pytest.mark.asyncio
async def test_audited_sync_decorator_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.audit_decorators import audited

    recorded: list[AuditStatus] = []

    def fake_schedule(**kwargs: Any) -> None:
        recorded.append(kwargs["status"])

    monkeypatch.setattr("app.services.audit.schedule_audit_event", fake_schedule)

    @audited("decorator.sync", AuditComponent.SERVICE)
    def work() -> None:
        raise RuntimeError("decorator boom")

    with pytest.raises(RuntimeError, match="decorator boom"):
        work()

    assert AuditStatus.FAILED in recorded


@pytest.mark.quick
def test_record_audit_sync_queues_event(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import audit as audit_mod

    recorded: list[str] = []

    def fake_schedule(**kwargs: Any) -> None:
        recorded.append(kwargs["action"])

    monkeypatch.setattr(audit_mod, "schedule_audit_event", fake_schedule)

    audit_mod.record_audit_sync(
        action="sync.manual",
        component=AuditComponent.SERVICE.value,
        status=AuditStatus.FAILED,
        message="manual failure",
    )

    assert recorded == ["sync.manual"]


@pytest.mark.quick
def test_extract_error_fields_includes_traceback() -> None:
    try:
        raise TypeError("bad type")
    except TypeError as exc:
        error_type, error_message, traceback_text = extract_error_fields(exc)

    assert error_type == "TypeError"
    assert error_message == "bad type"
    assert traceback_text is not None
    assert "TypeError" in traceback_text


@pytest.mark.quick
@pytest.mark.asyncio
async def test_persist_event_swallows_writer_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.services.audit_dispatch import _persist_event

    async def boom(_event: AuditEvent) -> int:
        raise ConnectionError("db down")

    monkeypatch.setattr("app.services.audit_dispatch.get_audit_writer", lambda: AsyncMock(write=boom))

    with caplog.at_level(logging.ERROR, logger="app.audit"):
        row_id = await _persist_event(
            AuditEvent.from_kwargs("test.persist", "service", AuditStatus.FAILED, error=RuntimeError("x"))
        )

    assert row_id is None
    assert any("Failed to persist audit event" in r.message for r in caplog.records)


@pytest.mark.quick
@pytest.mark.asyncio
async def test_postgres_writer_swallows_db_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.services.audit_backends.postgres import PostgresAuditWriter

    @asynccontextmanager
    async def broken_session():
        session = MagicMock()
        session.add = MagicMock()
        session.commit = AsyncMock(side_effect=ConnectionError("db down"))
        session.refresh = AsyncMock()
        yield session

    monkeypatch.setattr("app.services.audit_backends.postgres.db_session", broken_session)

    writer = PostgresAuditWriter()
    with caplog.at_level(logging.ERROR, logger="app.audit"):
        row_id = await writer.write(
            AuditEvent.from_kwargs("postgres.fail", AuditComponent.SERVICE.value, AuditStatus.FAILED)
        )

    assert row_id is None
    assert any("Failed to persist audit log" in r.message for r in caplog.records)


@pytest.mark.quick
@pytest.mark.asyncio
async def test_flush_pending_audit_tasks_waits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import settings
    from app.services.audit_backends.registry import reset_audit_writer, set_audit_writer
    from app.services.audit_dispatch import flush_pending_audit_tasks, schedule_audit_event

    writer = RecordingWriter()
    set_audit_writer(writer)
    monkeypatch.setattr(settings, "audit_enabled", True)
    monkeypatch.setattr(settings, "audit_blocking", False)

    schedule_audit_event("flush.test", AuditComponent.SERVICE.value, AuditStatus.SUCCESS)
    await flush_pending_audit_tasks(timeout=2.0)

    assert any(event.action == "flush.test" for event in writer.events)
    reset_audit_writer()


@pytest.mark.quick
def test_schedule_audit_event_no_loop_uses_background_thread(
    monkeypatch: pytest.MonkeyPatch,
    audit_writer: RecordingWriter,
) -> None:
    import time

    from app.services.audit_dispatch import schedule_audit_event

    monkeypatch.setattr(
        "app.services.audit_dispatch.asyncio.get_running_loop",
        MagicMock(side_effect=RuntimeError("no loop")),
    )

    schedule_audit_event("thread.test", AuditComponent.SERVICE.value, AuditStatus.SUCCESS)

    deadline = time.time() + 2
    while time.time() < deadline:
        if any(event.action == "thread.test" for event in audit_writer.events):
            break
        time.sleep(0.05)

    assert any(event.action == "thread.test" for event in audit_writer.events)


@pytest.mark.quick
def test_audit_enabled_false_skips_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings
    from app.services.audit_dispatch import schedule_audit_event

    monkeypatch.setattr(settings, "audit_enabled", False)

    result = schedule_audit_event("disabled.test", AuditComponent.SERVICE.value, AuditStatus.SUCCESS)
    assert result is None


@pytest.mark.quick
def test_background_job_failure_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """Job exceptions must propagate once — not re-run after audit_track records FAILED."""
    import ui.background_jobs as bg
    from ui.background_jobs import JobKind

    calls = {"count": 0}

    async def failing_coro(progress_callback):
        calls["count"] += 1
        raise ValueError("job failed once")

    monkeypatch.setattr(bg, "_session_key", lambda: "job-failure-session")

    job_id = bg.start_async_job(JobKind.TODAY_PREDICTION, "fail once", failing_coro)

    import time

    deadline = time.time() + 5
    while time.time() < deadline:
        with bg._lock:
            job = bg._jobs_for_session("job-failure-session").get(job_id)
        if job and job["status"] == "failed":
            break
        time.sleep(0.05)

    with bg._lock:
        job = bg._jobs_for_session("job-failure-session").get(job_id)

    assert job is not None
    assert job["status"] == "failed"
    assert calls["count"] == 1

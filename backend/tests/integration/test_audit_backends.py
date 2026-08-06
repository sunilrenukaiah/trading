"""ABC audit backend framework tests."""

from __future__ import annotations

import asyncio

import logging

import pytest

from app.services.audit_backends.base import AuditEvent
from app.services.audit_backends.composite import CompositeAuditWriter
from app.services.audit_backends.logging_backend import LoggingAuditWriter
from app.services.audit_backends.noop import NoOpAuditWriter
from app.services.audit_backends.registry import build_audit_writer, reset_audit_writer, set_audit_writer
from app.services.audit_types import AuditComponent, AuditStatus


class InMemoryAuditWriter:
    """Test double implementing the AuditWriter contract."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def write(self, event: AuditEvent) -> int | None:
        self.events.append(event)
        return len(self.events)


@pytest.mark.quick
@pytest.mark.asyncio
async def test_noop_writer() -> None:
    writer = NoOpAuditWriter()
    result = await writer.write(
        AuditEvent.from_kwargs("test", "service", AuditStatus.SUCCESS, message="ok")
    )
    assert result is None


@pytest.mark.quick
@pytest.mark.asyncio
async def test_composite_writer_fan_out() -> None:
    first = InMemoryAuditWriter()
    second = InMemoryAuditWriter()
    writer = CompositeAuditWriter([first, second])
    event = AuditEvent.from_kwargs("test.action", "service", AuditStatus.SUCCESS)

    row_id = await writer.write(event)
    assert row_id == 1
    assert len(first.events) == 1
    assert len(second.events) == 1


@pytest.mark.quick
@pytest.mark.asyncio
async def test_logging_writer_emits(caplog: pytest.LogCaptureFixture) -> None:
    writer = LoggingAuditWriter()
    with caplog.at_level(logging.INFO, logger="app.audit"):
        await writer.write(
            AuditEvent.from_kwargs(
                "test.fail",
                AuditComponent.SERVICE.value,
                AuditStatus.FAILED,
                message="boom",
                error=RuntimeError("boom"),
            )
        )
    assert any("test.fail" in r.message for r in caplog.records)
    assert any(r.levelno >= logging.ERROR for r in caplog.records)


@pytest.mark.quick
def test_build_audit_writer_composite(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "audit_enabled", True)
    monkeypatch.setattr(settings, "audit_backend", "composite")
    reset_audit_writer()
    writer = build_audit_writer()
    assert isinstance(writer, CompositeAuditWriter)


@pytest.mark.quick
@pytest.mark.asyncio
async def test_record_audit_uses_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings
    from app.services import audit as audit_mod

    memory = InMemoryAuditWriter()
    set_audit_writer(memory)
    monkeypatch.setattr(settings, "audit_blocking", True)

    row_id = await audit_mod.record_audit(
        "test.record",
        AuditComponent.SERVICE.value,
        AuditStatus.SUCCESS,
        message="done",
    )
    assert row_id == 1
    assert memory.events[0].action == "test.record"
    assert memory.events[0].status == AuditStatus.SUCCESS


@pytest.mark.quick
@pytest.mark.asyncio
async def test_schedule_audit_event_non_blocking(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.audit_dispatch import schedule_audit_event

    gate = asyncio.Event()

    async def slow_persist(event):
        await gate.wait()

    monkeypatch.setattr("app.services.audit_dispatch._persist_event", slow_persist)

    schedule_audit_event("test.async", AuditComponent.SERVICE.value, AuditStatus.SUCCESS)
    gate.set()


@pytest.mark.quick
def test_audit_logging_handler_records_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.audit_handlers import AuditLoggingHandler

    recorded: list[dict] = []

    def fake_record(**kwargs):
        recorded.append(kwargs)
        return 1

    monkeypatch.setattr("app.services.audit_dispatch.schedule_audit_event", fake_record)

    handler = AuditLoggingHandler()
    record = logging.LogRecord(
        name="app.ingestion",
        level=logging.ERROR,
        pathname=__file__,
        lineno=10,
        msg="sync failed",
        args=(),
        exc_info=None,
    )
    handler.emit(record)

    assert len(recorded) == 1
    assert recorded[0]["action"] == "log.app.ingestion"
    assert recorded[0]["status"] == AuditStatus.FAILED


@pytest.mark.quick
@pytest.mark.asyncio
async def test_audited_decorator_success(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.audit_decorators import audited

    recorded: list[AuditStatus] = []

    def fake_schedule(action, component, status, **kwargs):
        recorded.append(status)

    monkeypatch.setattr("app.services.audit.schedule_audit_event", fake_schedule)

    @audited("decorator.test", AuditComponent.SERVICE)
    async def work() -> str:
        return "ok"

    assert await work() == "ok"
    assert AuditStatus.STARTED in recorded
    assert AuditStatus.SUCCESS in recorded

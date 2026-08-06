"""Audit framework unit tests."""

from __future__ import annotations

import pytest

from app.services.audit_types import AuditComponent, AuditStatus, AuditSoftFailure, audit_status_for_http
from app.services.audit import _serialize_context, _truncate


@pytest.mark.quick
def test_truncate_long_text() -> None:
    assert _truncate("hello", 10) == "hello"
    assert _truncate("x" * 20, 10) == "xxxxxxx..."


@pytest.mark.quick
def test_serialize_context_safe() -> None:
    ctx = _serialize_context({"universe": "NIFTY250", "count": 17, "ok": True, "none": None})
    assert ctx["universe"] == "NIFTY250"
    assert ctx["count"] == 17


@pytest.mark.quick
@pytest.mark.asyncio
async def test_audit_track_success(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import audit as audit_mod

    recorded: list[tuple] = []

    def fake_schedule(action, component, status, **kwargs):
        recorded.append((status, kwargs.get("duration_ms")))

    monkeypatch.setattr(audit_mod, "schedule_audit_event", fake_schedule)

    async with audit_mod.audit_track("test.action", AuditComponent.SERVICE, foo="bar"):
        pass

    assert len(recorded) == 2
    assert recorded[0][0] == AuditStatus.STARTED
    assert recorded[1][0] == AuditStatus.SUCCESS
    assert recorded[1][1] is not None


@pytest.mark.quick
@pytest.mark.asyncio
async def test_audit_track_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import audit as audit_mod

    recorded: list[AuditStatus] = []

    def fake_schedule(action, component, status, **kwargs):
        recorded.append(status)

    monkeypatch.setattr(audit_mod, "schedule_audit_event", fake_schedule)

    with pytest.raises(ValueError, match="boom"):
        async with audit_mod.audit_track("test.fail", AuditComponent.SERVICE):
            raise ValueError("boom")

    assert AuditStatus.STARTED in recorded
    assert AuditStatus.FAILED in recorded


@pytest.mark.quick
def test_audit_status_for_http() -> None:
    assert audit_status_for_http(200) == AuditStatus.SUCCESS
    assert audit_status_for_http(404) == AuditStatus.CLIENT_ERROR
    assert audit_status_for_http(422) == AuditStatus.CLIENT_ERROR
    assert audit_status_for_http(500) == AuditStatus.FAILED


@pytest.mark.quick
@pytest.mark.asyncio
async def test_audit_track_soft_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import audit as audit_mod

    recorded: list[AuditStatus] = []

    def fake_schedule(action, component, status, **kwargs):
        recorded.append(status)

    monkeypatch.setattr(audit_mod, "schedule_audit_event", fake_schedule)

    with pytest.raises(AuditSoftFailure, match="no data"):
        async with audit_mod.audit_track("test.skip", AuditComponent.SERVICE):
            raise AuditSoftFailure("no data")

    assert AuditStatus.STARTED in recorded
    assert AuditStatus.SKIPPED in recorded
    assert AuditStatus.FAILED not in recorded

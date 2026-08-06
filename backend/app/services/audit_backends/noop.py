"""No-op audit writer when audit is disabled."""

from __future__ import annotations

from app.services.audit_backends.base import AuditEvent, AuditWriter


class NoOpAuditWriter(AuditWriter):
    async def write(self, event: AuditEvent) -> int | None:
        return None

"""Fan-out audit writer that delegates to multiple backends."""

from __future__ import annotations

from app.services.audit_backends.base import AuditEvent, AuditWriter


class CompositeAuditWriter(AuditWriter):
    """Write each event to every configured backend."""

    def __init__(self, writers: list[AuditWriter]) -> None:
        self._writers = writers

    async def write(self, event: AuditEvent) -> int | None:
        row_id: int | None = None
        for writer in self._writers:
            try:
                result = await writer.write(event)
                if row_id is None and result is not None:
                    row_id = result
            except Exception:
                # Individual backends must not raise, but guard composite anyway.
                continue
        return row_id

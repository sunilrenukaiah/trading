"""Stdlib logging backend for audit events."""

from __future__ import annotations

import logging

from app.services.audit_backends.base import AuditEvent, AuditWriter
from app.services.audit_types import AuditStatus

logger = logging.getLogger("app.audit")


class LoggingAuditWriter(AuditWriter):
    """Emit every audit event to the app.audit logger at an appropriate level."""

    async def write(self, event: AuditEvent) -> int | None:
        level = _level_for_status(event.status)
        parts = [
            f"action={event.action}",
            f"component={event.component}",
            f"status={event.status.value}",
        ]
        if event.duration_ms is not None:
            parts.append(f"duration_ms={event.duration_ms}")
        if event.correlation_id:
            parts.append(f"correlation_id={event.correlation_id}")
        if event.request_id:
            parts.append(f"request_id={event.request_id}")
        if event.session_id:
            parts.append(f"session_id={event.session_id}")

        summary = " ".join(parts)
        if event.message:
            summary = f"{summary} message={event.message}"

        extra = {"audit_context": event.context or {}}

        if event.status == AuditStatus.FAILED and event.traceback:
            logger.log(level, summary, extra=extra)
            logger.log(level, "traceback:\n%s", event.traceback, extra=extra)
        elif event.error_message:
            logger.log(level, "%s error=%s: %s", summary, event.error_type, event.error_message, extra=extra)
        else:
            logger.log(level, summary, extra=extra)
        return None


def _level_for_status(status: AuditStatus) -> int:
    if status == AuditStatus.FAILED:
        return logging.ERROR
    if status in {AuditStatus.CLIENT_ERROR, AuditStatus.SKIPPED}:
        return logging.WARNING
    if status == AuditStatus.STARTED:
        return logging.DEBUG
    return logging.INFO

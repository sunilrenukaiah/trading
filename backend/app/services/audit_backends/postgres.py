"""PostgreSQL audit writer and reader."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import desc, select

from app.db.ui_session import db_session
from app.services.audit_backends.base import AuditEvent, AuditReader, AuditWriter
from app.services.audit_types import AuditStatus

logger = logging.getLogger("app.audit")


def _audit_log_model():
    """Resolve AuditLog class without reloading the full ORM model graph."""
    from app.models.audit_log import AuditLog

    return AuditLog


class PostgresAuditWriter(AuditWriter, AuditReader):
    """Persist audit events to the audit_logs table."""

    async def write(self, event: AuditEvent) -> int | None:
        AuditLog = _audit_log_model()
        row = AuditLog(
            action=event.action,
            component=event.component,
            status=event.status.value,
            duration_ms=event.duration_ms,
            message=event.message,
            error_type=event.error_type,
            error_message=event.error_message,
            traceback=event.traceback,
            context=event.context or {},
            session_id=event.session_id,
            request_id=event.request_id,
            correlation_id=event.correlation_id,
        )

        try:
            async with db_session() as session:
                session.add(row)
                await session.commit()
                await session.refresh(row)
                return row.id
        except Exception:
            logger.exception("Failed to persist audit log for action=%s", event.action)
            return None

    async def list_logs(
        self,
        *,
        limit: int = 100,
        action_prefix: str | None = None,
        status: AuditStatus | None = None,
        component: str | None = None,
    ) -> list[Any]:
        AuditLog = _audit_log_model()
        async with db_session() as session:
            query = select(AuditLog).order_by(desc(AuditLog.created_at)).limit(min(limit, 500))
            if action_prefix:
                query = query.where(AuditLog.action.startswith(action_prefix))
            if status:
                query = query.where(AuditLog.status == status.value)
            if component:
                query = query.where(AuditLog.component == component)
            return list((await session.scalars(query)).all())

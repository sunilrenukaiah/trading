"""Abstract base classes for the audit framework."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.services.audit_types import AuditStatus


@dataclass
class AuditEvent:
    """Normalized audit record passed to all writers."""

    action: str
    component: str
    status: AuditStatus
    duration_ms: int | None = None
    message: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    traceback: str | None = None
    context: dict[str, Any] = field(default_factory=dict)
    session_id: str | None = None
    request_id: str | None = None
    correlation_id: str | None = None

    @classmethod
    def from_kwargs(
        cls,
        action: str,
        component: str,
        status: AuditStatus,
        *,
        duration_ms: int | None = None,
        message: str | None = None,
        error: BaseException | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
        traceback_text: str | None = None,
        context: dict[str, Any] | None = None,
        session_id: str | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AuditEvent:
        from app.services.audit_backends.serializers import (
            extract_error_fields,
            new_correlation_id,
            serialize_context,
            truncate,
        )

        if error is not None:
            err_type, err_msg, tb = extract_error_fields(error)
            error_type = error_type or err_type
            error_message = error_message or err_msg
            traceback_text = traceback_text or tb

        return cls(
            action=action,
            component=component,
            status=status,
            duration_ms=duration_ms,
            message=truncate(message, 2000),
            error_type=error_type,
            error_message=error_message,
            traceback=traceback_text,
            context=serialize_context(context),
            session_id=session_id,
            request_id=request_id,
            correlation_id=correlation_id or new_correlation_id(),
        )


class AuditWriter(ABC):
    """Persist or emit one audit event. Implementations must never raise."""

    @abstractmethod
    async def write(self, event: AuditEvent) -> int | None:
        """Return a row id when applicable, else None."""


class AuditReader(ABC):
    """Query persisted audit events."""

    @abstractmethod
    async def list_logs(
        self,
        *,
        limit: int = 100,
        action_prefix: str | None = None,
        status: AuditStatus | None = None,
        component: str | None = None,
    ) -> list[Any]:
        ...

"""Persist and query audit events with timing and exception capture."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager, contextmanager
from typing import Any, AsyncIterator, Iterator

from app.config import settings
from app.services.audit_backends.base import AuditEvent
from app.services.audit_backends.registry import get_audit_reader
from app.services.audit_backends.serializers import new_correlation_id, serialize_context
from app.services.audit_dispatch import schedule_audit_event
from app.services.audit_types import AuditComponent, AuditStatus, AuditSoftFailure

# Backward-compatible re-exports for tests and callers.
from app.services.audit_backends.serializers import serialize_context as _serialize_context
from app.services.audit_backends.serializers import truncate as _truncate


def _is_audit_soft_failure(exc: BaseException) -> bool:
    """True for AuditSoftFailure even if modules were reloaded (Streamlit/tests)."""
    return any(cls.__name__ == "AuditSoftFailure" for cls in type(exc).__mro__)


async def record_audit(
    action: str,
    component: str,
    status: AuditStatus,
    *,
    duration_ms: int | None = None,
    message: str | None = None,
    error: BaseException | None = None,
    context: dict[str, Any] | None = None,
    session_id: str | None = None,
    request_id: str | None = None,
    correlation_id: str | None = None,
) -> int | None:
    """Queue one audit row (non-blocking). Awaits only when audit_blocking=True."""
    if not getattr(settings, "audit_enabled", True):
        return None

    if getattr(settings, "audit_blocking", False):
        from app.services.audit_dispatch import _persist_event

        event = AuditEvent.from_kwargs(
            action,
            component,
            status,
            duration_ms=duration_ms,
            message=message,
            error=error,
            context=context,
            session_id=session_id,
            request_id=request_id,
            correlation_id=correlation_id,
        )
        return await _persist_event(event)

    schedule_audit_event(
        action,
        component,
        status,
        duration_ms=duration_ms,
        message=message,
        error=error,
        context=context,
        session_id=session_id,
        request_id=request_id,
        correlation_id=correlation_id,
    )
    return None


def record_audit_sync(**kwargs: Any) -> int | None:
    """Queue audit from sync code — never blocks the caller."""
    return schedule_audit_event(**kwargs)


@asynccontextmanager
async def audit_track(
    action: str,
    component: str | AuditComponent,
    *,
    log_started: bool = True,
    session_id: str | None = None,
    request_id: str | None = None,
    **context: Any,
) -> AsyncIterator[dict[str, Any]]:
    """
    Async context manager: business logic runs first; audit is queued after.

    STARTED is scheduled (not awaited) before yield. SUCCESS/FAILED are
    scheduled after the block completes — never blocking the operation.
    """
    comp = component.value if isinstance(component, AuditComponent) else component
    correlation_id = new_correlation_id()
    ctx = serialize_context(context)
    start = time.perf_counter()
    audit_ctx = {"correlation_id": correlation_id, **ctx}

    if log_started:
        schedule_audit_event(
            action,
            comp,
            AuditStatus.STARTED,
            context=ctx,
            session_id=session_id,
            request_id=request_id,
            correlation_id=correlation_id,
        )

    try:
        yield audit_ctx
    except Exception as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
        if isinstance(exc, AuditSoftFailure) or _is_audit_soft_failure(exc):
            schedule_audit_event(
                action,
                comp,
                getattr(exc, "audit_status", AuditStatus.SKIPPED),
                duration_ms=duration_ms,
                message=str(exc),
                context=audit_ctx,
                session_id=session_id,
                request_id=request_id,
                correlation_id=correlation_id,
            )
        else:
            schedule_audit_event(
                action,
                comp,
                AuditStatus.FAILED,
                duration_ms=duration_ms,
                message=f"{action} failed",
                error=exc,
                context=audit_ctx,
                session_id=session_id,
                request_id=request_id,
                correlation_id=correlation_id,
            )
        raise
    else:
        duration_ms = int((time.perf_counter() - start) * 1000)
        schedule_audit_event(
            action,
            comp,
            AuditStatus.SUCCESS,
            duration_ms=duration_ms,
            message=f"{action} completed in {duration_ms}ms",
            context=audit_ctx,
            session_id=session_id,
            request_id=request_id,
            correlation_id=correlation_id,
        )


@contextmanager
def audit_track_sync(
    action: str,
    component: str | AuditComponent,
    *,
    log_started: bool = True,
    session_id: str | None = None,
    request_id: str | None = None,
    **context: Any,
) -> Iterator[dict[str, Any]]:
    """Sync context manager — audit queued, never blocking."""
    comp = component.value if isinstance(component, AuditComponent) else component
    correlation_id = new_correlation_id()
    ctx = serialize_context(context)
    start = time.perf_counter()
    audit_ctx = {"correlation_id": correlation_id, **ctx}

    if log_started:
        schedule_audit_event(
            action=action,
            component=comp,
            status=AuditStatus.STARTED,
            context=ctx,
            session_id=session_id,
            request_id=request_id,
            correlation_id=correlation_id,
        )

    try:
        yield audit_ctx
    except Exception as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
        if isinstance(exc, AuditSoftFailure) or _is_audit_soft_failure(exc):
            schedule_audit_event(
                action=action,
                component=comp,
                status=getattr(exc, "audit_status", AuditStatus.SKIPPED),
                duration_ms=duration_ms,
                message=str(exc),
                context=audit_ctx,
                session_id=session_id,
                request_id=request_id,
                correlation_id=correlation_id,
            )
        else:
            schedule_audit_event(
                action=action,
                component=comp,
                status=AuditStatus.FAILED,
                duration_ms=duration_ms,
                message=f"{action} failed",
                error=exc,
                context=audit_ctx,
                session_id=session_id,
                request_id=request_id,
                correlation_id=correlation_id,
            )
        raise
    else:
        duration_ms = int((time.perf_counter() - start) * 1000)
        schedule_audit_event(
            action=action,
            component=comp,
            status=AuditStatus.SUCCESS,
            duration_ms=duration_ms,
            message=f"{action} completed in {duration_ms}ms",
            context=audit_ctx,
            session_id=session_id,
            request_id=request_id,
            correlation_id=correlation_id,
        )


async def list_audit_logs(
    *,
    limit: int = 100,
    action_prefix: str | None = None,
    status: AuditStatus | None = None,
    component: str | None = None,
) -> list[Any]:
    return await get_audit_reader().list_logs(
        limit=limit,
        action_prefix=action_prefix,
        status=status,
        component=component,
    )

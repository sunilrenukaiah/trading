"""Fire-and-forget audit dispatch — never blocks business logic."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from app.config import settings
from app.services.audit_backends.base import AuditEvent
from app.services.audit_backends.registry import get_audit_writer
from app.services.audit_types import AuditStatus

logger = logging.getLogger("app.audit")

_pending_tasks: set[asyncio.Task[Any]] = set()
_tasks_lock = threading.Lock()


async def _persist_event(event: AuditEvent) -> int | None:
    try:
        return await get_audit_writer().write(event)
    except Exception:
        logger.exception("Failed to persist audit event action=%s", event.action)
        return None


def schedule_audit_event(
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
    blocking: bool = False,
) -> int | None:
    """
    Queue an audit write without delaying the caller.

    When ``blocking=True`` (tests/debug only), waits for persistence.
    """
    if not getattr(settings, "audit_enabled", True):
        return None

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

    if blocking or getattr(settings, "audit_blocking", False):
        return _run_blocking(_persist_event(event))

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _schedule_on_background_loop(event)
        return None

    task = loop.create_task(_persist_event(event))
    with _tasks_lock:
        _pending_tasks.add(task)

    def _done(t: asyncio.Task[Any]) -> None:
        with _tasks_lock:
            _pending_tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.debug("Background audit task failed: %s", exc)

    task.add_done_callback(_done)
    return None


def _schedule_on_background_loop(event: AuditEvent) -> None:
    try:
        from ui.async_runner import fire_and_forget_audit

        fire_and_forget_audit(_persist_event(event))
        return
    except ImportError:
        pass

    def _run() -> None:
        try:
            asyncio.run(_persist_event(event))
        except Exception:
            logger.exception("Failed background audit for action=%s", event.action)

    threading.Thread(target=_run, name="audit-writer", daemon=True).start()


def _run_blocking(coro: Any) -> int | None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    if loop.is_running():
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return future.result(timeout=10)
        except Exception:
            logger.exception("Blocking audit write failed")
            return None
    return asyncio.run(coro)


async def flush_pending_audit_tasks(timeout: float = 5.0) -> None:
    """Test helper — wait for queued audit tasks to finish."""
    with _tasks_lock:
        tasks = list(_pending_tasks)
    if not tasks:
        return
    await asyncio.wait(tasks, timeout=timeout)

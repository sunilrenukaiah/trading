"""@audited decorator for sync and async functions."""

from __future__ import annotations

import asyncio
import functools
from typing import Any, Callable, TypeVar

from app.services.audit import audit_track, audit_track_sync
from app.services.audit_types import AuditComponent

F = TypeVar("F", bound=Callable[..., Any])


def audited(
    action: str,
    component: str | AuditComponent,
    *,
    log_started: bool = True,
    **context: Any,
) -> Callable[[F], F]:
    """Wrap a function with audit_track / audit_track_sync."""

    def decorator(fn: F) -> F:
        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                async with audit_track(
                    action,
                    component,
                    log_started=log_started,
                    **context,
                ):
                    return await fn(*args, **kwargs)

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            with audit_track_sync(
                action,
                component,
                log_started=log_started,
                **context,
            ):
                return fn(*args, **kwargs)

        return sync_wrapper  # type: ignore[return-value]

    return decorator

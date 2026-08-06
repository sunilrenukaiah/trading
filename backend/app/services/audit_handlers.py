"""Global hooks that capture exceptions and ERROR logs into the audit DB."""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from typing import Any

from app.config import settings
from app.services.audit_types import AuditComponent, AuditStatus

_hooks_installed = False
_handler_installed = False
_lock = threading.Lock()

_ORIGINAL_EXCEPTHOOK = sys.excepthook
_ORIGINAL_ASYNCIO_HANDLER: Any = None


def _record_from_logging(
    *,
    action: str,
    message: str,
    error: BaseException | None = None,
    context: dict[str, Any] | None = None,
) -> None:
    from app.services.audit_dispatch import schedule_audit_event

    schedule_audit_event(
        action=action,
        component=AuditComponent.SERVICE.value,
        status=AuditStatus.FAILED,
        message=message,
        error=error,
        context=context,
    )


class AuditLoggingHandler(logging.Handler):
    """Capture ERROR/CRITICAL stdlib log records into audit_logs."""

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.ERROR:
            return
        # Avoid recursion when the audit logger itself fails.
        if record.name.startswith("app.audit"):
            return

        exc: BaseException | None = None
        if record.exc_info and record.exc_info[1] is not None:
            exc = record.exc_info[1]  # type: ignore[assignment]

        context = {
            "logger": record.name,
            "level": record.levelname,
            "pathname": record.pathname,
            "lineno": record.lineno,
            "funcName": record.funcName,
        }

        try:
            _record_from_logging(
                action=f"log.{record.name}",
                message=record.getMessage(),
                error=exc,
                context=context,
            )
        except Exception:
            pass


def _sys_excepthook(exc_type, exc_value, exc_tb) -> None:
    if exc_value is not None:
        try:
            _record_from_logging(
                action="sys.unhandled_exception",
                message=str(exc_value),
                error=exc_value if isinstance(exc_value, BaseException) else None,
                context={"exc_type": getattr(exc_type, "__name__", str(exc_type))},
            )
        except Exception:
            pass
    _ORIGINAL_EXCEPTHOOK(exc_type, exc_value, exc_tb)


def _asyncio_exception_handler(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
    exc = context.get("exception")
    message = context.get("message", "asyncio unhandled exception")
    try:
        _record_from_logging(
            action="asyncio.unhandled_exception",
            message=str(message),
            error=exc if isinstance(exc, BaseException) else None,
            context={k: str(v) for k, v in context.items() if k != "exception"},
        )
    except Exception:
        pass

    default_handler = _ORIGINAL_ASYNCIO_HANDLER
    if default_handler is not None:
        default_handler(loop, context)
    else:
        loop.default_exception_handler(context)


def reset_audit_hooks_for_tests() -> None:
    """Restore stdlib hooks/handlers between tests."""
    global _hooks_installed, _handler_installed, _ORIGINAL_ASYNCIO_HANDLER

    with _lock:
        sys.excepthook = _ORIGINAL_EXCEPTHOOK
        root = logging.getLogger()
        for handler in list(root.handlers):
            if isinstance(handler, AuditLoggingHandler):
                root.removeHandler(handler)
        _hooks_installed = False
        _handler_installed = False
        _ORIGINAL_ASYNCIO_HANDLER = None


def install_audit_hooks(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """Install stdlib logging handler and global exception hooks once."""
    global _hooks_installed, _handler_installed, _ORIGINAL_ASYNCIO_HANDLER

    if not getattr(settings, "audit_enabled", True):
        return

    with _lock:
        if getattr(settings, "audit_capture_log_errors", True) and not _handler_installed:
            root = logging.getLogger()
            if not any(isinstance(h, AuditLoggingHandler) for h in root.handlers):
                handler = AuditLoggingHandler()
                handler.setLevel(logging.ERROR)
                root.addHandler(handler)
            _handler_installed = True

        if getattr(settings, "audit_capture_unhandled_exceptions", True) and not _hooks_installed:
            sys.excepthook = _sys_excepthook
            target_loop = loop
            if target_loop is None:
                try:
                    target_loop = asyncio.get_running_loop()
                except RuntimeError:
                    try:
                        target_loop = asyncio.get_event_loop()
                    except RuntimeError:
                        target_loop = None
            if target_loop is not None:
                if _ORIGINAL_ASYNCIO_HANDLER is None:
                    _ORIGINAL_ASYNCIO_HANDLER = target_loop.get_exception_handler()
                target_loop.set_exception_handler(_asyncio_exception_handler)
            _hooks_installed = True

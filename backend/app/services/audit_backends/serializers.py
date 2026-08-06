"""Shared audit event serialization helpers."""

from __future__ import annotations

import traceback
import uuid
from typing import Any

from app.config import settings
from app.services.audit_types import AuditStatus


def truncate(text: str | None, max_chars: int) -> str | None:
    if text is None:
        return None
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def serialize_context(context: dict[str, Any] | None) -> dict[str, Any]:
    if not context:
        return {}
    safe: dict[str, Any] = {}
    for key, value in context.items():
        try:
            if isinstance(value, (str, int, float, bool)) or value is None:
                safe[key] = value
            elif isinstance(value, (list, tuple)):
                safe[key] = list(value)[:50]
            else:
                safe[key] = str(value)
        except Exception:
            safe[key] = "<unserializable>"
    return safe


def extract_error_fields(error: BaseException | None) -> tuple[str | None, str | None, str | None]:
    if error is None:
        return None, None, None
    error_type = type(error).__name__
    error_message = truncate(str(error), 2000)
    tb_text = truncate(
        "".join(traceback.format_exception(type(error), error, error.__traceback__)),
        getattr(settings, "audit_traceback_max_chars", 4000),
    )
    return error_type, error_message, tb_text


def new_correlation_id() -> str:
    return str(uuid.uuid4())[:12]

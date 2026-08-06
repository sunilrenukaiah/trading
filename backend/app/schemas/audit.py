"""Audit log API schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    action: str
    component: str
    status: str
    duration_ms: int | None
    message: str | None
    error_type: str | None
    error_message: str | None
    traceback: str | None
    context: dict[str, Any] | None
    session_id: str | None
    request_id: str | None
    correlation_id: str | None
    created_at: datetime

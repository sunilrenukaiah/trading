"""Audit logging — timings, actions, errors."""

from __future__ import annotations

import enum


class AuditStatus(str, enum.Enum):
    STARTED = "STARTED"
    SUCCESS = "SUCCESS"
    CLIENT_ERROR = "CLIENT_ERROR"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


def audit_status_for_http(status_code: int) -> AuditStatus:
    """Map HTTP status to audit status (4xx = client error, 5xx = server failure)."""
    if status_code < 400:
        return AuditStatus.SUCCESS
    if status_code < 500:
        return AuditStatus.CLIENT_ERROR
    return AuditStatus.FAILED


class AuditSoftFailure(Exception):
    """Expected non-success outcome that should not be logged as FAILED."""

    audit_status: AuditStatus = AuditStatus.SKIPPED

    def __init__(self, message: str, *, audit_status: AuditStatus | None = None) -> None:
        super().__init__(message)
        if audit_status is not None:
            self.audit_status = audit_status


class InsufficientBacktestDataError(AuditSoftFailure):
    """Not enough OHLCV history to run or persist a simulation."""


class AuditComponent(str, enum.Enum):
    API = "api"
    UI = "ui"
    SERVICE = "service"
    JOB = "job"
    INGESTION = "ingestion"

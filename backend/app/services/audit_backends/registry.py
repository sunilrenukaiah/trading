"""Audit writer factory and singleton registry."""

from __future__ import annotations

from app.config import settings
from app.services.audit_backends.base import AuditReader, AuditWriter
from app.services.audit_backends.composite import CompositeAuditWriter
from app.services.audit_backends.logging_backend import LoggingAuditWriter
from app.services.audit_backends.noop import NoOpAuditWriter
from app.services.audit_backends.postgres import PostgresAuditWriter

_writer: AuditWriter | None = None
_reader: AuditReader | None = None


def build_audit_writer() -> AuditWriter:
    if not getattr(settings, "audit_enabled", True):
        return NoOpAuditWriter()

    backend = getattr(settings, "audit_backend", "composite").lower()
    postgres = PostgresAuditWriter()

    if backend == "noop":
        return NoOpAuditWriter()
    if backend == "postgres":
        return postgres
    if backend == "logging":
        return LoggingAuditWriter()
    if backend == "composite":
        return CompositeAuditWriter([postgres, LoggingAuditWriter()])

    return CompositeAuditWriter([postgres, LoggingAuditWriter()])


def get_audit_writer() -> AuditWriter:
    global _writer
    if _writer is None:
        _writer = build_audit_writer()
    return _writer


def get_audit_reader() -> AuditReader:
    global _reader
    if _reader is None:
        _reader = PostgresAuditWriter()
    return _reader


def reset_audit_writer() -> None:
    global _writer, _reader
    _writer = None
    _reader = None


def set_audit_writer(writer: AuditWriter) -> None:
    global _writer
    _writer = writer

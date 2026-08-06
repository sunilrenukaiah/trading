"""Optional PostgreSQL schema isolation for trading-lab (LAB_MODE=1)."""

from __future__ import annotations

import re

from sqlalchemy import event
from sqlalchemy.engine import Engine

_VALID_SCHEMA = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def register_lab_search_path(engine: Engine, schema: str | None) -> None:
    """Set search_path on each new connection when running in lab schema mode."""
    if not schema or not _VALID_SCHEMA.match(schema):
        return

    @event.listens_for(engine, "connect")
    def _set_search_path(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute(f"SET search_path TO {schema}, public")
        cursor.close()

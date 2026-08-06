"""Streamlit-only async DB — engine is created on the UI event loop (NullPool).

asyncpg connections must be used on the loop that created them. The FastAPI
engine uses a connection pool on uvicorn's loop; Streamlit must not share it.

Background jobs may install a *thread-local* engine via ``install_isolated_session``
so long-running work does not contend with the UI event loop / exclusive lock.
"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db.lab_schema import register_lab_search_path

_ui_engine: AsyncEngine | None = None
UISessionLocal: async_sessionmaker[AsyncSession] | None = None
_tls = threading.local()


def _local_factory() -> async_sessionmaker[AsyncSession] | None:
    return getattr(_tls, "UISessionLocal", None)


def _local_engine() -> AsyncEngine | None:
    return getattr(_tls, "_ui_engine", None)


def install_isolated_session(
    factory: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
) -> None:
    """Bind a job-local session factory to the current thread only."""
    _tls.UISessionLocal = factory
    _tls._ui_engine = engine


def clear_isolated_session() -> None:
    """Clear thread-local session binding (does not dispose the engine)."""
    _tls.UISessionLocal = None
    _tls._ui_engine = None


async def ensure_ui_db() -> async_sessionmaker[AsyncSession]:
    """Create/return the session factory for the current thread or UI loop."""
    local = _local_factory()
    if local is not None:
        return local

    global _ui_engine, UISessionLocal

    if UISessionLocal is not None:
        return UISessionLocal

    _ui_engine = create_async_engine(
        settings.database_url,
        poolclass=NullPool,
        echo=False,
        connect_args={"prepared_statement_cache_size": 0},
    )
    register_lab_search_path(
        _ui_engine.sync_engine,
        settings.lab_schema if settings.lab_mode else None,
    )
    UISessionLocal = async_sessionmaker(_ui_engine, class_=AsyncSession, expire_on_commit=False)
    return UISessionLocal


async def dispose_ui_db() -> None:
    global _ui_engine, UISessionLocal
    if _ui_engine is not None:
        await _ui_engine.dispose()
    _ui_engine = None
    UISessionLocal = None


def ui_db_initialized() -> bool:
    return _local_factory() is not None or UISessionLocal is not None


async def get_ui_engine() -> AsyncEngine:
    local_engine = _local_engine()
    if local_engine is not None:
        return local_engine
    factory = await ensure_ui_db()
    engine = factory.kw.get("bind") if hasattr(factory, "kw") else getattr(factory, "bind", None)
    if engine is None:
        raise RuntimeError("UI session factory has no bound engine")
    return engine


@asynccontextmanager
async def ui_session() -> AsyncIterator[AsyncSession]:
    factory = await ensure_ui_db()
    async with factory() as session:
        yield session


@asynccontextmanager
async def db_session() -> AsyncIterator[AsyncSession]:
    """Prefer the UI / isolated session factory when initialized."""
    local = _local_factory()
    if local is not None:
        async with local() as session:
            yield session
        return
    if UISessionLocal is not None:
        async with UISessionLocal() as session:
            yield session
    else:
        from app.db.session import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            yield session

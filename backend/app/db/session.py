from __future__ import annotations

import asyncio
import sys
import threading

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db.lab_schema import register_lab_search_path


def _engine_kwargs() -> dict:
    """Under pytest, avoid pooled connections tied to another event loop."""
    if "pytest" in sys.modules:
        return {"poolclass": NullPool}
    return {
        "pool_pre_ping": True,
        "pool_size": 5,
        "max_overflow": 10,
        "pool_reset_on_return": "rollback",
    }


engine = create_async_engine(
    settings.database_url,
    echo=False,
    **_engine_kwargs(),
)
register_lab_search_path(engine.sync_engine, settings.lab_schema if settings.lab_mode else None)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def dispose_engine() -> None:
    await engine.dispose()


def dispose_engine_sync(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """Best-effort pool dispose from sync code (Streamlit runner / recovery / tests)."""
    if loop is not None and loop.is_running():
        try:
            future = asyncio.run_coroutine_threadsafe(dispose_engine(), loop)
            future.result(timeout=30)
            return
        except Exception:
            pass

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(dispose_engine())
        return

    # pytest-asyncio / anyio may leave a running loop in the main thread.
    errors: list[BaseException] = []

    def _dispose_on_fresh_loop() -> None:
        try:
            asyncio.run(dispose_engine())
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=_dispose_on_fresh_loop, name="dispose-engine", daemon=True)
    thread.start()
    thread.join(timeout=30)
    if errors:
        raise errors[0]


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

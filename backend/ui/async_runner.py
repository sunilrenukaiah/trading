"""Single background event loop for all Streamlit async DB/API work.

asyncpg connections are bound to one event loop. All DB coroutines must run
exclusively on this loop — never via asyncio.run() or unguarded
run_coroutine_threadsafe() from other threads.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
from concurrent.futures import Future
from typing import Any, Callable, Coroutine, TypeVar

T = TypeVar("T")

CoroFactory = Callable[[], Coroutine[Any, Any, T]]
CoroInput = Coroutine[Any, Any, T] | CoroFactory

_loop: asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None
_ready = threading.Event()
_thread_lock = threading.RLock()
_async_db_lock: asyncio.Lock | None = None

# Long backfills (NIFTY250) can run many minutes.
_DEFAULT_TIMEOUT_SEC = 60 * 60


def _is_asyncpg_interface_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "interfaceerror" in type(exc).__name__.lower() or "another operation is in progress" in msg


def _dispose_engine_pool() -> None:
    """Drop UI connections after loop restart or asyncpg InterfaceError."""
    if _loop is None or not _loop.is_running():
        return
    try:
        from app.db.ui_session import dispose_ui_db

        future = asyncio.run_coroutine_threadsafe(dispose_ui_db(), _loop)
        future.result(timeout=30)
    except Exception:
        pass


def _loop_thread_main() -> None:
    global _loop, _async_db_lock
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _async_db_lock = asyncio.Lock()
    _ready.set()
    _loop.run_forever()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _thread
    if _loop is not None and _loop.is_running():
        return _loop

    if _loop is not None:
        _dispose_engine_pool()
    _ready.clear()
    _thread = threading.Thread(target=_loop_thread_main, name="streamlit-async", daemon=True)
    _thread.start()
    _ready.wait()
    assert _loop is not None
    return _loop


async def _run_exclusive(coro: Coroutine[Any, Any, T]) -> T:
    assert _async_db_lock is not None
    async with _async_db_lock:
        return await coro


def _resolve_coro(coro_or_factory: CoroInput[T]) -> Coroutine[Any, Any, T]:
    if inspect.iscoroutine(coro_or_factory):
        return coro_or_factory
    if callable(coro_or_factory):
        resolved = coro_or_factory()
        if not inspect.iscoroutine(resolved):
            raise TypeError("Callable passed to run_async must return a coroutine")
        return resolved
    raise TypeError("run_async expects a coroutine or callable that returns a coroutine")


def run_async(
    coro_or_factory: CoroInput[T],
    *,
    timeout: float | None = _DEFAULT_TIMEOUT_SEC,
    retries: int = 1,
) -> T:
    """Run a coroutine on the shared background loop (exclusive DB access)."""
    loop = _ensure_loop()
    is_factory = callable(coro_or_factory) and not inspect.iscoroutine(coro_or_factory)
    max_attempts = (retries + 1) if is_factory else 1
    last_exc: BaseException | None = None

    for attempt in range(max_attempts):
        with _thread_lock:
            try:
                coro = _resolve_coro(coro_or_factory)
                if not inspect.iscoroutine(coro):
                    raise TypeError(
                        f"run_async expected a coroutine, got {type(coro)!r}. "
                        "Pass an async call like run_async(my_coro()) not run_async(lambda: ...)"
                        " unless ui.async_runner is up to date."
                    )
                future: Future[T] = asyncio.run_coroutine_threadsafe(_run_exclusive(coro), loop)
                return future.result(timeout=timeout)
            except Exception as exc:
                last_exc = exc
                if attempt < max_attempts - 1 and _is_asyncpg_interface_error(exc):
                    _dispose_engine_pool()
                    continue
                raise

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("run_async failed without exception")


def fire_and_forget_audit(coro: Coroutine[Any, Any, T]) -> None:
    """Schedule audit coroutine on the background loop without waiting."""
    loop = _ensure_loop()
    asyncio.run_coroutine_threadsafe(_run_exclusive(coro), loop)


def submit_audit(coro: Coroutine[Any, Any, T], *, timeout: float = 10) -> T:
    """Schedule audit (or other short) coroutines through the same exclusive guard."""
    return run_async(coro, timeout=timeout, retries=0)


async def _run_with_isolated_engine(coro_or_factory: CoroInput[T]) -> T:
    """Create a NullPool engine on *this* loop, bind it thread-locally, run coro."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.config import settings
    from app.db import ui_session as ui_mod
    from app.db.lab_schema import register_lab_search_path

    engine = create_async_engine(
        settings.database_url,
        poolclass=NullPool,
        echo=False,
        connect_args={"prepared_statement_cache_size": 0},
    )
    register_lab_search_path(
        engine.sync_engine,
        settings.lab_schema if settings.lab_mode else None,
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    ui_mod.install_isolated_session(factory, engine)
    try:
        return await _resolve_coro(coro_or_factory)
    finally:
        ui_mod.clear_isolated_session()
        await engine.dispose()


def run_isolated_async(
    coro_or_factory: CoroInput[T],
    *,
    timeout: float | None = _DEFAULT_TIMEOUT_SEC,
) -> T:
    """Run long background work on a dedicated event loop + DB engine.

    Unlike ``run_async``, this does **not** take the UI exclusive lock and does
    not schedule onto the Streamlit UI loop — so Hard refresh / recommendations
    cannot freeze page navigation or blank the browser tab.
    """
    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            coro = _run_with_isolated_engine(coro_or_factory)
            if timeout is None:
                result["value"] = loop.run_until_complete(coro)
            else:
                result["value"] = loop.run_until_complete(asyncio.wait_for(coro, timeout=timeout))
        except BaseException as exc:  # noqa: BLE001 — surface to caller
            error["exc"] = exc
        finally:
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            loop.close()
            asyncio.set_event_loop(None)

    # Always run on a fresh thread so we never nest loops if called from UI loop thread.
    worker = threading.Thread(target=_runner, name="isolated-async-job", daemon=True)
    worker.start()
    # Join with timeout+buffer so the caller can surface TimeoutError cleanly.
    join_timeout = None if timeout is None else float(timeout) + 30.0
    worker.join(timeout=join_timeout)
    if worker.is_alive():
        raise TimeoutError(f"run_isolated_async exceeded {timeout}s")
    if "exc" in error:
        raise error["exc"]
    return result["value"]


def reset_for_tests() -> None:
    """Stop the background loop between tests (pytest only)."""
    global _loop, _thread, _async_db_lock

    if _loop is not None and _loop.is_running():
        _loop.call_soon_threadsafe(_loop.stop)

    if _thread is not None and _thread.is_alive():
        _thread.join(timeout=5)

    _loop = None
    _thread = None
    _async_db_lock = None
    _ready.clear()

    try:
        from app.db import ui_session

        ui_session._ui_engine = None
        ui_session.UISessionLocal = None
        ui_session.clear_isolated_session()
    except Exception:
        pass

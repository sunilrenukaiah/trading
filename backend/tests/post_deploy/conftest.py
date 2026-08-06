"""Fixtures for post-deployment smoke tests."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent


def _postgres_reachable() -> bool:
    url = os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://trading:trading@localhost:5432/trading",
    )
    if not url.startswith("postgresql"):
        return False
    try:
        import asyncio

        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool

        async def _ping() -> None:
            engine = create_async_engine(url, poolclass=NullPool)
            try:
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
            finally:
                await engine.dispose()

        asyncio.run(_ping())
        return True
    except Exception:
        return False


def _run_migrations() -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        check=True,
        capture_output=True,
    )


@pytest.fixture(scope="session")
def deploy_api_base_url() -> str:
    return os.environ.get("POST_DEPLOY_API_URL", "").rstrip("/")


@pytest.fixture(scope="session")
def postgres_available() -> bool:
    return _postgres_reachable()


@pytest.fixture(scope="session", autouse=True)
def _reset_api_engine_pool_before_post_deploy() -> None:
    """Drop stale asyncpg connections after integration tests (different event loops)."""
    from app.db.session import dispose_engine_sync

    dispose_engine_sync()
    yield
    dispose_engine_sync()


@pytest.fixture(scope="session")
def api_client(deploy_api_base_url: str, postgres_available: bool):
    """Remote httpx client (POST_DEPLOY_API_URL) or in-process TestClient with DB."""
    if deploy_api_base_url:
        with httpx.Client(base_url=deploy_api_base_url, timeout=120.0) as client:
            yield ("remote", client)
        return

    if not postgres_available:
        pytest.skip("PostgreSQL not reachable — set DATABASE_URL or POST_DEPLOY_API_URL")

    _run_migrations()

    from fastapi.testclient import TestClient

    from app.main import app

    # Post-deploy checks own DB readiness; skip heavy startup backfill.
    app.router.on_startup.clear()

    with TestClient(app) as client:
        yield ("local", client)


@pytest.fixture(scope="session")
def run_mutating_post_deploy() -> bool:
    return os.environ.get("POST_DEPLOY_RUN_MUTATING", "").lower() in {"1", "true", "yes"}

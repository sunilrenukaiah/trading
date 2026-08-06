"""FastAPI smoke tests without requiring a live database."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.mark.quick
def test_health_endpoint_without_startup_db() -> None:
    from app.main import app

    # Avoid startup backfill hitting Postgres during smoke test.
    app.router.on_startup.clear()

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.quick
def test_backtest_patterns_route_registered() -> None:
    from app.main import app

    schema = app.openapi()
    paths = schema.get("paths", {})

    assert "/health" in {route.path for route in app.routes if getattr(route, "path", None)}
    assert "/api/backtest/patterns" in paths

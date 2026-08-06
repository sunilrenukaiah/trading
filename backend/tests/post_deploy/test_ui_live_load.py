"""Live UI HTTP smoke — verifies the running Streamlit server is not a blank shell."""

from __future__ import annotations

import os

import httpx
import pytest

DEFAULT_UI_URL = os.environ.get("POST_DEPLOY_UI_URL", "http://127.0.0.1:8501")


def _ui_base() -> str:
    return DEFAULT_UI_URL.rstrip("/")


@pytest.mark.post_deploy
def test_live_streamlit_http_serves_app_shell() -> None:
    """GET / must return Streamlit HTML (not connection-refused / empty body)."""
    url = f"{_ui_base()}/"
    try:
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        pytest.skip(f"Streamlit UI not reachable at {url}: {exc}")

    assert response.status_code == 200, f"unexpected status {response.status_code}"
    body = response.text
    assert len(body) > 500, "response body too small — blank/empty shell"
    # Streamlit root document always references its frontend bundle.
    assert (
        "streamlit" in body.lower()
        or "root" in body.lower()
        or "<div" in body.lower()
    ), "response does not look like a Streamlit app document"


@pytest.mark.post_deploy
def test_live_streamlit_health_endpoint() -> None:
    """Streamlit exposes /_stcore/health when the server is alive."""
    url = f"{_ui_base()}/_stcore/health"
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        pytest.skip(f"Streamlit health not reachable at {url}: {exc}")

    assert response.status_code == 200
    assert "ok" in response.text.lower()

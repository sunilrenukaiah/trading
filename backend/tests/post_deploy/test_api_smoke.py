"""Post-deployment API smoke tests — every route must not return 5xx."""

from __future__ import annotations

import re

import pytest

from tests.post_deploy.api_catalog import (
    ALL_ENDPOINTS,
    GET_ENDPOINTS,
    MUTATING_ENDPOINTS,
    ApiEndpoint,
)


def _paths_match(concrete_path: str, template_path: str) -> bool:
    pattern = re.sub(r"\{[^}]+\}", r"[^/]+", template_path)
    return re.fullmatch(pattern, concrete_path) is not None


def _openapi_paths(client_tuple) -> set[str]:
    _kind, client = client_tuple
    response = client.get("/openapi.json")
    assert response.status_code == 200
    return set(response.json().get("paths", {}))


def _request(client_tuple, endpoint: ApiEndpoint):
    _kind, client = client_tuple
    method = endpoint.method.upper()
    kwargs = {}
    if endpoint.json_body is not None:
        kwargs["json"] = endpoint.json_body

    if _kind == "remote":
        return client.request(method, endpoint.path, **kwargs)
    return client.request(method, endpoint.path, **kwargs)


def _assert_endpoint_ok(client_tuple, endpoint: ApiEndpoint) -> None:
    response = _request(client_tuple, endpoint)
    assert response.status_code < 500, (
        f"{endpoint.name} {endpoint.method} {endpoint.path} returned server error "
        f"{response.status_code}: {response.text[:500]}"
    )
    assert response.status_code in endpoint.allowed_statuses, (
        f"{endpoint.name} {endpoint.method} {endpoint.path} returned unexpected "
        f"{response.status_code} (allowed {sorted(endpoint.allowed_statuses)}): "
        f"{response.text[:300]}"
    )


@pytest.mark.post_deploy
@pytest.mark.parametrize("endpoint", GET_ENDPOINTS, ids=lambda e: e.name)
def test_get_endpoint_not_server_error(api_client, endpoint: ApiEndpoint) -> None:
    _assert_endpoint_ok(api_client, endpoint)


@pytest.mark.post_deploy
def test_openapi_lists_all_get_routes(api_client) -> None:
    paths = _openapi_paths(api_client)
    for endpoint in GET_ENDPOINTS:
        if endpoint.path == "/health":
            continue
        base_path = endpoint.path.split("?")[0]
        assert any(_paths_match(base_path, p) for p in paths), (
            f"OpenAPI missing route for {base_path}"
        )


@pytest.mark.post_deploy
@pytest.mark.parametrize("endpoint", MUTATING_ENDPOINTS, ids=lambda e: e.name)
def test_mutating_endpoint_not_server_error(
    api_client,
    endpoint: ApiEndpoint,
    run_mutating_post_deploy: bool,
) -> None:
    if endpoint.skip_by_default and not run_mutating_post_deploy:
        pytest.skip(f"Set POST_DEPLOY_RUN_MUTATING=1 to run {endpoint.name}")
    _assert_endpoint_ok(api_client, endpoint)


@pytest.mark.post_deploy
def test_api_catalog_covers_registered_routes() -> None:
    from app.main import app

    schema_paths = set(app.openapi()["paths"])
    missing = []
    for endpoint in ALL_ENDPOINTS:
        if endpoint.path == "/health":
            continue
        base_path = endpoint.path.split("?")[0]
        if not any(_paths_match(base_path, p) for p in schema_paths):
            missing.append(base_path)
    assert not missing, f"Post-deploy catalog paths not in OpenAPI: {missing}"

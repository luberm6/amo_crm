"""
Tests for API key authentication.

Covers:
- Auth disabled (api_key=""): all requests pass through
- Auth enabled: missing key → 401, wrong key → 403, correct key → 200/201
- Read-only endpoints (GET) are not protected by default
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_call_engine
from app.db.session import get_db
from app.integrations.call_engine.stub import StubEngine
from app.main import create_app


@pytest.fixture
def app_with_auth(session) -> FastAPI:
    """FastAPI app with API key auth enabled."""
    from unittest.mock import patch
    import app.core.config as cfg

    application = create_app()

    async def override_get_db():
        yield session

    application.dependency_overrides[get_db] = override_get_db
    return application, "test-secret-key-12345"


@pytest.mark.anyio
async def test_create_call_no_auth_when_key_empty(client: AsyncClient):
    """When API_KEY is empty, POST /v1/calls requires no auth."""
    # Default fixture has api_key="" → auth disabled
    resp = await client.post("/v1/calls", json={"phone": "+79991234567"})
    assert resp.status_code == 201


@pytest.mark.anyio
async def test_create_call_with_auth_missing_key(session):
    """When API_KEY is set, missing header → 401."""
    from unittest.mock import patch
    import app.core.config as cfg

    app = create_app()

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db

    with patch.object(cfg.settings, "api_key", "secret-key"):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post("/v1/calls", json={"phone": "+79991234567"})
            assert resp.status_code == 401


@pytest.mark.anyio
async def test_create_call_with_auth_wrong_key(session):
    """When API_KEY is set, wrong key → 403."""
    from unittest.mock import patch
    import app.core.config as cfg

    app = create_app()

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db

    with patch.object(cfg.settings, "api_key", "secret-key"):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                "/v1/calls",
                json={"phone": "+79991234567"},
                headers={"x-api-key": "wrong-key"},
            )
            assert resp.status_code == 403


@pytest.mark.anyio
async def test_create_call_with_correct_api_key(session):
    """When API_KEY is set, correct key → 201."""
    from unittest.mock import patch
    import app.core.config as cfg

    app = create_app()

    async def override_get_db():
        yield session

    async def override_get_call_engine():
        from app.integrations.call_engine.router_engine import RoutingCallEngine
        return RoutingCallEngine(
            vapi_engine=None,
            direct_engine=None,
            browser_engine=None,
            fallback_engine=StubEngine(),
        )

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_call_engine] = override_get_call_engine

    with patch.object(cfg.settings, "api_key", "secret-key"):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                "/v1/calls",
                json={"phone": "+79991234567"},
                headers={"x-api-key": "secret-key"},
            )
            assert resp.status_code == 201


@pytest.mark.anyio
async def test_get_endpoints_do_not_require_auth(client: AsyncClient):
    """GET /health, GET /ready, GET /v1/calls/active are publicly readable."""
    for path in ["/health", "/ready", "/v1/calls/active"]:
        resp = await client.get(path)
        assert resp.status_code in (200, 503), f"{path} should not require auth"

"""
Tests for middleware.

Covers:
- RequestIdMiddleware: X-Request-ID echoed in response, custom ID propagated
- SecurityHeadersMiddleware: required security headers present in responses
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient


# ── Request ID ────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_request_id_header_present(client: AsyncClient):
    """Every response has an X-Request-ID header."""
    resp = await client.get("/health")
    assert "x-request-id" in resp.headers or "X-Request-ID" in resp.headers


@pytest.mark.anyio
async def test_request_id_custom_value_echoed(client: AsyncClient):
    """If caller sends X-Request-ID, it's echoed back."""
    custom_id = "my-correlation-id-12345"
    resp = await client.get("/health", headers={"X-Request-ID": custom_id})
    assert resp.headers.get("x-request-id") == custom_id


@pytest.mark.anyio
async def test_request_id_generated_when_missing(client: AsyncClient):
    """When no X-Request-ID sent, server generates a UUID."""
    resp = await client.get("/health")
    request_id = resp.headers.get("x-request-id")
    assert request_id is not None
    assert len(request_id) > 0


# ── Security Headers ──────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_security_headers_present(client: AsyncClient):
    """Required security headers are present on all responses."""
    resp = await client.get("/health")
    headers = {k.lower(): v for k, v in resp.headers.items()}

    assert headers.get("x-content-type-options") == "nosniff"
    assert headers.get("x-frame-options") == "DENY"
    assert "x-xss-protection" in headers


@pytest.mark.anyio
async def test_security_headers_on_404(client: AsyncClient):
    """Security headers are present even on 404 responses."""
    import uuid
    resp = await client.get(f"/v1/calls/{uuid.uuid4()}")
    headers = {k.lower(): v for k, v in resp.headers.items()}
    assert headers.get("x-content-type-options") == "nosniff"
    assert headers.get("x-frame-options") == "DENY"

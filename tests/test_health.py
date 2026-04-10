"""
Smoke tests for health endpoints and basic call lifecycle.
"""

import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_health(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_ready_db_ok(client: AsyncClient) -> None:
    """
    /ready should succeed when the DB is reachable.
    Redis check will fail in unit tests (no real Redis) but DB should be ok.
    """
    resp = await client.get("/ready")
    data = resp.json()
    # DB must be accessible; Redis may be degraded in unit test environment
    assert data["db"] == "ok"


@pytest.mark.anyio
async def test_create_call(client: AsyncClient) -> None:
    resp = await client.post("/v1/calls", json={"phone": "+79991234567"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["phone"] == "+79991234567"
    assert data["status"] == "QUEUED"  # StubEngine sets QUEUED as initial status
    assert "id" in data


@pytest.mark.anyio
async def test_create_call_invalid_phone(client: AsyncClient) -> None:
    resp = await client.post("/v1/calls", json={"phone": "not-a-phone"})
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_get_active_calls(client: AsyncClient) -> None:
    # Create a call first
    await client.post("/v1/calls", json={"phone": "+79991234568"})
    resp = await client.get("/v1/calls/active")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert data["total"] >= 1


@pytest.mark.anyio
async def test_get_call_not_found(client: AsyncClient) -> None:
    resp = await client.get("/v1/calls/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_steer_call(client: AsyncClient) -> None:
    create_resp = await client.post("/v1/calls", json={"phone": "+79991234569"})
    call_id = create_resp.json()["id"]

    steer_resp = await client.post(
        f"/v1/calls/{call_id}/steer",
        json={"instruction": "Ask about budget", "issued_by": "12345678"},
    )
    assert steer_resp.status_code == 201
    data = steer_resp.json()
    assert data["instruction"] == "Ask about budget"
    assert data["call_id"] == call_id


@pytest.mark.anyio
async def test_stop_call(client: AsyncClient) -> None:
    create_resp = await client.post("/v1/calls", json={"phone": "+79991234560"})
    call_id = create_resp.json()["id"]

    stop_resp = await client.post(f"/v1/calls/{call_id}/stop")
    assert stop_resp.status_code == 200
    assert stop_resp.json()["status"] == "STOPPED"

    # Stopping again should be idempotent
    stop_again = await client.post(f"/v1/calls/{call_id}/stop")
    assert stop_again.status_code == 200
    assert stop_again.json()["status"] == "STOPPED"

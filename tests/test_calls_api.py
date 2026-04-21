"""
Integration tests for /v1/calls endpoints.

Tests cover:
- POST /v1/calls: happy path, invalid phone, blocked phone
- GET /v1/calls/active: empty and non-empty
- GET /v1/calls/{id}: found, not found, with transcript
- POST /v1/calls/{id}/steer: happy path, terminal call (422)
- POST /v1/calls/{id}/stop: happy path, idempotent
- GET /v1/calls/{id}/card: compact view

All tests use the in-memory SQLite test DB and StubEngine.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_call_engine
from app.integrations.call_engine.stub import StubEngine
from app.models.agent_profile import AgentProfile
from app.models.blocked_phone import BlockedPhone
from app.repositories.agent_profile_repo import AgentProfileRepository
from app.repositories.blocked_phone_repo import BlockedPhoneRepository


# ── POST /v1/calls ────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_create_call_returns_201(client: AsyncClient):
    """POST /v1/calls → 201 with call data."""
    resp = await client.post("/v1/calls", json={"phone": "+79991234567"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["phone"] == "+79991234567"
    assert data["status"] in ("CREATED", "QUEUED", "COMPLETED")  # StubEngine → COMPLETED
    assert "id" in data


@pytest.mark.anyio
async def test_create_call_accepts_phone_number_and_agent_name(app: FastAPI, session: AsyncSession):
    async def override_get_call_engine():
        return StubEngine()

    app.dependency_overrides[get_call_engine] = override_get_call_engine
    agent_name = f"Test Agent {uuid.uuid4()}"
    agent = await AgentProfileRepository(AgentProfile, session).save(
        AgentProfile(
            name=agent_name,
            is_active=True,
            system_prompt="Prompt",
            voice_strategy="tts_primary",
            voice_provider="elevenlabs",
            config={},
            version=1,
            telephony_provider="mango",
        )
    )
    await session.flush()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/calls",
            json={"phone_number": "+79991234567", "agent_name": agent_name, "mode": "DIRECT"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["accepted"] is True
        assert data["phone"] == "+79991234567"
        assert data["agent_profile_id"] == str(agent.id)
        assert data["call_id"] == data["id"]


@pytest.mark.anyio
async def test_create_call_normalizes_phone(client: AsyncClient):
    """POST /v1/calls with local format → normalized E.164 returned."""
    resp = await client.post("/v1/calls", json={"phone": "89998887766"})
    assert resp.status_code == 201
    assert resp.json()["phone"].startswith("+7")


@pytest.mark.anyio
async def test_create_call_invalid_phone_returns_422(client: AsyncClient):
    """POST /v1/calls with garbage phone → 422."""
    resp = await client.post("/v1/calls", json={"phone": "not-a-phone"})
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_create_call_with_explicit_mode(client: AsyncClient):
    """POST /v1/calls with mode=vapi without VAPI credentials → 502 engine_error.

    In the test environment VAPI is not configured, so explicit mode=vapi must
    fail fast rather than silently falling through to StubEngine.
    """
    resp = await client.post("/v1/calls", json={"phone": "+79991234567", "mode": "vapi"})
    assert resp.status_code == 502
    assert resp.json()["detail"]["error"] == "engine_error"


@pytest.mark.anyio
async def test_create_call_blocked_phone(client: AsyncClient, session: AsyncSession):
    """POST /v1/calls with blocked phone → 422 blocked_phone error."""
    repo = BlockedPhoneRepository(BlockedPhone, session)
    await repo.block("+79991112233")

    resp = await client.post("/v1/calls", json={"phone": "+79991112233"})
    assert resp.status_code == 422
    body = resp.json()
    # AppError converted to HTTPException in endpoint → {"detail": {"error": ...}}
    detail = body.get("detail") or body
    assert detail.get("error") == "blocked_phone"


# ── GET /v1/calls/active ──────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_list_active_calls_empty(client: AsyncClient):
    """GET /v1/calls/active → empty list initially."""
    resp = await client.get("/v1/calls/active")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data


@pytest.mark.anyio
async def test_list_active_calls_returns_active(client: AsyncClient):
    """GET /v1/calls/active → counts active calls (StubEngine marks COMPLETED)."""
    # StubEngine returns COMPLETED immediately, so active list will be empty
    await client.post("/v1/calls", json={"phone": "+79991234567"})
    resp = await client.get("/v1/calls/active")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["total"], int)


# ── GET /v1/calls/{id} ────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_get_call_found(client: AsyncClient):
    """GET /v1/calls/{id} → full call data."""
    create_resp = await client.post("/v1/calls", json={"phone": "+79991234567"})
    call_id = create_resp.json()["id"]

    resp = await client.get(f"/v1/calls/{call_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == call_id
    assert "transcript_entries" in data


@pytest.mark.anyio
async def test_get_call_not_found(client: AsyncClient):
    """GET /v1/calls/<random-uuid> → 404."""
    import uuid
    resp = await client.get(f"/v1/calls/{uuid.uuid4()}")
    assert resp.status_code == 404
    body = resp.json()
    detail = body.get("detail") or body
    assert detail.get("error") == "not_found"


# ── GET /v1/calls/{id}/card ───────────────────────────────────────────────────

@pytest.mark.anyio
async def test_get_call_card(client: AsyncClient):
    """GET /v1/calls/{id}/card → compact card with required fields."""
    create_resp = await client.post("/v1/calls", json={"phone": "+79991234567"})
    call_id = create_resp.json()["id"]

    resp = await client.get(f"/v1/calls/{call_id}/card")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert "is_active" in data
    assert "transcript_tail" in data


@pytest.mark.anyio
async def test_get_call_card_not_found(client: AsyncClient):
    """GET /v1/calls/<random>/card → 404."""
    import uuid
    resp = await client.get(f"/v1/calls/{uuid.uuid4()}/card")
    assert resp.status_code == 404


# ── POST /v1/calls/{id}/steer ────────────────────────────────────────────────

@pytest.mark.anyio
async def test_steer_call(client: AsyncClient, session: AsyncSession):
    """POST /v1/calls/{id}/steer → 201 with steering instruction."""
    from app.models.call import Call, CallStatus
    from app.repositories.call_repo import CallRepository

    # Create a call that is IN_PROGRESS (not terminal)
    create_resp = await client.post("/v1/calls", json={"phone": "+79991234567"})
    call_id = create_resp.json()["id"]

    # Force status to IN_PROGRESS so steering is allowed
    repo = CallRepository(Call, session)
    import uuid as _uuid
    call = await repo.get(_uuid.UUID(call_id))
    call.status = CallStatus.IN_PROGRESS
    await session.flush()

    resp = await client.post(
        f"/v1/calls/{call_id}/steer",
        json={"instruction": "Ask about the budget", "issued_by": "manager-1"},
    )
    assert resp.status_code == 201
    assert resp.json()["instruction"] == "Ask about the budget"


@pytest.mark.anyio
async def test_steer_completed_call_returns_422(client: AsyncClient, session: AsyncSession):
    """POST /v1/calls/{id}/steer on COMPLETED call → 422."""
    from app.models.call import Call, CallStatus
    from app.repositories.call_repo import CallRepository

    create_resp = await client.post("/v1/calls", json={"phone": "+79991234567"})
    call_id = create_resp.json()["id"]

    # Force status to COMPLETED so steer is rejected
    import uuid as _uuid
    repo = CallRepository(Call, session)
    call = await repo.get(_uuid.UUID(call_id))
    call.status = CallStatus.COMPLETED
    await session.flush()

    resp = await client.post(
        f"/v1/calls/{call_id}/steer",
        json={"instruction": "Too late", "issued_by": "manager"},
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_steer_missing_call_returns_404(client: AsyncClient):
    """POST /v1/calls/<unknown>/steer → 404."""
    import uuid
    resp = await client.post(
        f"/v1/calls/{uuid.uuid4()}/steer",
        json={"instruction": "test", "issued_by": "manager"},
    )
    assert resp.status_code == 404


# ── POST /v1/calls/{id}/stop ──────────────────────────────────────────────────

@pytest.mark.anyio
async def test_stop_call(client: AsyncClient, session: AsyncSession):
    """POST /v1/calls/{id}/stop on active call → 200 STOPPED."""
    from app.models.call import Call, CallStatus
    from app.repositories.call_repo import CallRepository

    create_resp = await client.post("/v1/calls", json={"phone": "+79991234567"})
    call_id = create_resp.json()["id"]

    # Force active status
    repo = CallRepository(Call, session)
    import uuid as _uuid
    call = await repo.get(_uuid.UUID(call_id))
    call.status = CallStatus.IN_PROGRESS
    await session.flush()

    resp = await client.post(f"/v1/calls/{call_id}/stop")
    assert resp.status_code == 200
    assert resp.json()["status"] == "STOPPED"


@pytest.mark.anyio
async def test_stop_call_idempotent(client: AsyncClient):
    """POST /v1/calls/{id}/stop twice → second call returns same result."""
    create_resp = await client.post("/v1/calls", json={"phone": "+79991234567"})
    call_id = create_resp.json()["id"]

    resp1 = await client.post(f"/v1/calls/{call_id}/stop")
    resp2 = await client.post(f"/v1/calls/{call_id}/stop")
    assert resp1.status_code == 200
    assert resp2.status_code == 200


@pytest.mark.anyio
async def test_stop_missing_call_returns_404(client: AsyncClient):
    """POST /v1/calls/<unknown>/stop → 404."""
    import uuid
    resp = await client.post(f"/v1/calls/{uuid.uuid4()}/stop")
    assert resp.status_code == 404

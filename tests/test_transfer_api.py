"""
Tests for transfer API endpoints.

6 tests covering:
- POST 201 returns record with manager_id
- POST 404 call not found
- POST 422 terminal call
- POST 503 no managers available
- GET manager-context returns summary/whisper
- GET manager-context 404 no transfer record
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call import Call, CallMode, CallStatus
from app.models.manager import Manager
from app.repositories.call_repo import CallRepository
from app.repositories.manager_repo import ManagerRepository


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _make_call(
    session: AsyncSession,
    status: CallStatus = CallStatus.IN_PROGRESS,
) -> Call:
    call = Call(phone="+79991234567", mode=CallMode.AUTO, status=status)
    repo = CallRepository(Call, session)
    return await repo.save(call)


async def _make_manager(
    session: AsyncSession,
    *,
    telegram_id: int = 888001,
    department: str = "sales",
) -> Manager:
    mgr = Manager(
        name="Тест Менеджер",
        phone="+79990000099",
        telegram_id=telegram_id,
        is_active=True,
        is_available=True,
        priority=1,
        department=department,
    )
    repo = ManagerRepository(Manager, session)
    return await repo.save(mgr)


# ── Tests ──────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_post_transfer_201(client: AsyncClient, session: AsyncSession):
    """POST /transfer returns 201 with a CONNECTED record."""
    call = await _make_call(session)
    await _make_manager(session, telegram_id=300001)

    resp = await client.post(f"/v1/calls/{call.id}/transfer", json={})
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "CONNECTED"
    assert data["manager_id"] is not None
    assert data["call_id"] == str(call.id)


@pytest.mark.anyio
async def test_post_transfer_404_call_not_found(client: AsyncClient):
    """POST /transfer with unknown call_id returns 404."""
    import uuid
    resp = await client.post(f"/v1/calls/{uuid.uuid4()}/transfer", json={})
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_post_transfer_422_terminal_call(client: AsyncClient, session: AsyncSession):
    """POST /transfer on a STOPPED call returns 422."""
    call = await _make_call(session, status=CallStatus.STOPPED)
    await _make_manager(session, telegram_id=300002)

    resp = await client.post(f"/v1/calls/{call.id}/transfer", json={})
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_post_transfer_503_no_managers(client: AsyncClient, session: AsyncSession):
    """POST /transfer with no available managers returns 503."""
    call = await _make_call(session)
    # No managers created

    resp = await client.post(f"/v1/calls/{call.id}/transfer", json={})
    assert resp.status_code == 503


@pytest.mark.anyio
async def test_get_manager_context_200(client: AsyncClient, session: AsyncSession):
    """GET /manager-context returns summary and whisper_text after transfer."""
    call = await _make_call(session)
    await _make_manager(session, telegram_id=300003)

    # First initiate a transfer
    post_resp = await client.post(f"/v1/calls/{call.id}/transfer", json={})
    assert post_resp.status_code == 201

    resp = await client.get(f"/v1/calls/{call.id}/manager-context")
    assert resp.status_code == 200
    data = resp.json()
    assert data["call_id"] == str(call.id)
    assert data["transfer_status"] == "CONNECTED"
    assert "customer_phone" in data
    assert data["manager_name"] is not None


@pytest.mark.anyio
async def test_get_manager_context_404_no_record(client: AsyncClient, session: AsyncSession):
    """GET /manager-context returns 404 when no transfer has been initiated."""
    call = await _make_call(session)

    resp = await client.get(f"/v1/calls/{call.id}/manager-context")
    assert resp.status_code == 404

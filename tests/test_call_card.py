"""
Tests for GET /calls/{id}/card endpoint and supporting infrastructure.

Covers:
- Normal call card with transcript + steering
- Empty transcript
- Empty steering history
- Invalid call_id (404)
- duration_seconds computation
- is_active flag
- tail parameter limiting transcript entries
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call import Call, CallMode, CallStatus
from app.models.steering import SteeringInstruction
from app.models.transcript import TranscriptEntry, TranscriptRole
from app.repositories.call_repo import CallRepository
from app.repositories.steering_repo import SteeringRepository
from app.repositories.transcript_repo import TranscriptRepository


# ── Helpers ────────────────────────────────────────────────────────────────────

_steering_timestamp_counter = 0

async def _make_call(
    session: AsyncSession,
    status: CallStatus = CallStatus.IN_PROGRESS,
    phone: str = "+79991234567",
    mode: CallMode = CallMode.AUTO,
) -> Call:
    call = Call(phone=phone, mode=mode, status=status)
    repo = CallRepository(Call, session)
    return await repo.save(call)


async def _add_transcript(
    session: AsyncSession,
    call: Call,
    entries: list[tuple[TranscriptRole, str]],
) -> None:
    repo = TranscriptRepository(TranscriptEntry, session)
    for role, text in entries:
        await repo.append(call.id, role, text)


async def _add_steering(
    session: AsyncSession, call: Call, instruction: str
) -> None:
    global _steering_timestamp_counter
    # Use explicit timestamps to avoid collisions in SQLite
    base_time = datetime.now(timezone.utc)
    entry_time = base_time + timedelta(milliseconds=_steering_timestamp_counter)
    _steering_timestamp_counter += 1

    entry = SteeringInstruction(
        call_id=call.id, instruction=instruction, issued_by="test",
        created_at=entry_time
    )
    session.add(entry)
    await session.flush()


# ── /card endpoint tests ──────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_card_basic_fields(client: AsyncClient, session: AsyncSession) -> None:
    """Card returns all expected fields for an active call."""
    call = await _make_call(session, status=CallStatus.IN_PROGRESS)
    await session.commit()

    resp = await client.get(f"/v1/calls/{call.id}/card")
    assert resp.status_code == 200
    data = resp.json()

    assert data["id"] == str(call.id)
    assert data["phone"] == "+79991234567"
    assert data["status"] == "IN_PROGRESS"
    assert data["is_active"] is True
    assert data["transcript_tail"] == []
    assert data["last_instruction"] is None
    assert data["summary"] is None


@pytest.mark.anyio
async def test_card_includes_transcript_tail(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Card should return last N transcript entries."""
    call = await _make_call(session)
    await _add_transcript(
        session,
        call,
        [
            (TranscriptRole.ASSISTANT, "Добрый день!"),
            (TranscriptRole.USER, "Алло"),
            (TranscriptRole.ASSISTANT, "Расскажите о бюджете"),
            (TranscriptRole.USER, "Около 50 тысяч"),
            (TranscriptRole.ASSISTANT, "Отлично!"),
            (TranscriptRole.USER, "Хорошо"),
        ],
    )
    await session.commit()

    # Default tail=5
    resp = await client.get(f"/v1/calls/{call.id}/card")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["transcript_tail"]) == 5
    # Should be the LAST 5, not first 5
    assert data["transcript_tail"][0]["text"] == "Алло"
    assert data["transcript_tail"][-1]["text"] == "Хорошо"


@pytest.mark.anyio
async def test_card_tail_param(client: AsyncClient, session: AsyncSession) -> None:
    """?tail=N controls how many transcript entries are returned."""
    call = await _make_call(session)
    await _add_transcript(
        session,
        call,
        [(TranscriptRole.USER, f"Entry {i}") for i in range(10)],
    )
    await session.commit()

    resp = await client.get(f"/v1/calls/{call.id}/card?tail=3")
    assert resp.status_code == 200
    assert len(resp.json()["transcript_tail"]) == 3
    # Last 3
    texts = [e["text"] for e in resp.json()["transcript_tail"]]
    assert texts == ["Entry 7", "Entry 8", "Entry 9"]


@pytest.mark.anyio
async def test_card_includes_last_instruction(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Card shows the most recent steering instruction."""
    call = await _make_call(session)
    await _add_steering(session, call, "Первая директива")
    await _add_steering(session, call, "Вторая директива")
    await session.commit()

    resp = await client.get(f"/v1/calls/{call.id}/card")
    assert resp.status_code == 200
    # Should return the LATEST instruction
    assert resp.json()["last_instruction"] == "Вторая директива"


@pytest.mark.anyio
async def test_card_not_found(client: AsyncClient) -> None:
    """Non-existent call returns 404."""
    fake_id = "00000000-0000-0000-0000-000000000000"
    resp = await client.get(f"/v1/calls/{fake_id}/card")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_card_is_active_false_for_stopped(
    client: AsyncClient, session: AsyncSession
) -> None:
    """is_active is False for terminal calls — disables control buttons."""
    call = await _make_call(session, status=CallStatus.STOPPED)
    await session.commit()

    resp = await client.get(f"/v1/calls/{call.id}/card")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_active"] is False
    assert data["status"] == "STOPPED"


@pytest.mark.anyio
async def test_card_completed_call_has_summary(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Completed calls may include summary and sentiment."""
    call = Call(
        phone="+79991234567",
        mode=CallMode.AUTO,
        status=CallStatus.COMPLETED,
        summary="Клиент заинтересован, запросил КП",
        sentiment="positive",
    )
    repo = CallRepository(Call, session)
    await repo.save(call)
    await session.commit()

    resp = await client.get(f"/v1/calls/{call.id}/card")
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"] == "Клиент заинтересован, запросил КП"
    assert data["sentiment"] == "positive"
    assert data["is_active"] is False


# ── Steering repository tests ──────────────────────────────────────────────────

@pytest.mark.anyio
async def test_steering_repo_get_last(session: AsyncSession) -> None:
    """get_last_for_call returns the most recent instruction."""
    call = await _make_call(session)
    await _add_steering(session, call, "Первая")
    await _add_steering(session, call, "Вторая")
    await _add_steering(session, call, "Третья")
    await session.commit()

    repo = SteeringRepository(SteeringInstruction, session)
    last = await repo.get_last_for_call(call.id)
    assert last is not None
    assert last.instruction == "Третья"


@pytest.mark.anyio
async def test_steering_repo_get_all_ordered(session: AsyncSession) -> None:
    """get_all_for_call returns instructions in chronological order."""
    call = await _make_call(session)
    for i in range(3):
        await _add_steering(session, call, f"Директива {i}")
    await session.commit()

    repo = SteeringRepository(SteeringInstruction, session)
    all_instrs = await repo.get_all_for_call(call.id)
    assert [s.instruction for s in all_instrs] == [
        "Директива 0",
        "Директива 1",
        "Директива 2",
    ]


@pytest.mark.anyio
async def test_steering_repo_none_when_empty(session: AsyncSession) -> None:
    """get_last_for_call returns None when no instructions exist."""
    call = await _make_call(session)
    await session.commit()

    repo = SteeringRepository(SteeringInstruction, session)
    last = await repo.get_last_for_call(call.id)
    assert last is None


# ── /active empty list test ────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_active_empty(client: AsyncClient, session: AsyncSession) -> None:
    """GET /calls/active returns empty list when all calls are terminal."""
    # Ensure any existing calls are terminal
    call = await _make_call(session, status=CallStatus.COMPLETED)
    await session.commit()

    resp = await client.get("/v1/calls/active")
    assert resp.status_code == 200
    data = resp.json()
    # The completed call should NOT appear
    active_ids = [item["id"] for item in data["items"]]
    assert str(call.id) not in active_ids


# ── /steer endpoint tests ─────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_steer_stores_instruction(
    client: AsyncClient, session: AsyncSession
) -> None:
    """POST /calls/{id}/steer stores the instruction and returns it."""
    call = await _make_call(session, status=CallStatus.IN_PROGRESS)
    await session.commit()

    resp = await client.post(
        f"/v1/calls/{call.id}/steer",
        json={"instruction": "Уточни потребности", "issued_by": "12345"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["instruction"] == "Уточни потребности"
    assert data["issued_by"] == "12345"
    assert data["call_id"] == str(call.id)


@pytest.mark.anyio
async def test_steer_terminal_call_returns_422(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Cannot steer a terminal call."""
    call = await _make_call(session, status=CallStatus.STOPPED)
    await session.commit()

    resp = await client.post(
        f"/v1/calls/{call.id}/steer",
        json={"instruction": "Too late", "issued_by": "system"},
    )
    assert resp.status_code == 422

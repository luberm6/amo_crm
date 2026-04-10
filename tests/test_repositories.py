"""
Repository layer tests.

Tests cover:
- CallRepository: get, save, get_active_calls, get_by_vapi_id
- BlockedPhoneRepository: full CRUD
- TranscriptRepository: append, get_by_call, sequence ordering
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.blocked_phone import BlockedPhone
from app.models.call import Call, CallMode, CallStatus
from app.models.transcript import TranscriptEntry, TranscriptRole
from app.repositories.blocked_phone_repo import BlockedPhoneRepository
from app.repositories.call_repo import CallRepository
from app.repositories.transcript_repo import TranscriptRepository


# ── CallRepository ────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_call_repo_save_and_get(session: AsyncSession):
    """save() persists call; get() retrieves it by ID."""
    repo = CallRepository(Call, session)
    call = Call(phone="+79991234567", mode=CallMode.AUTO, status=CallStatus.CREATED)
    saved = await repo.save(call)
    assert saved.id is not None

    fetched = await repo.get(saved.id)
    assert fetched is not None
    assert fetched.phone == "+79991234567"


@pytest.mark.anyio
async def test_call_repo_get_missing_returns_none(session: AsyncSession):
    """get() with unknown UUID returns None."""
    repo = CallRepository(Call, session)
    result = await repo.get(uuid.uuid4())
    assert result is None


@pytest.mark.anyio
async def test_call_repo_get_active_calls(session: AsyncSession):
    """get_active_calls() returns only non-terminal calls."""
    repo = CallRepository(Call, session)

    active = Call(phone="+79991234561", mode=CallMode.AUTO, status=CallStatus.IN_PROGRESS)
    terminal = Call(phone="+79991234562", mode=CallMode.AUTO, status=CallStatus.COMPLETED)
    await repo.save(active)
    await repo.save(terminal)

    results = await repo.get_active_calls()
    active_phones = [c.phone for c in results]
    assert "+79991234561" in active_phones
    assert "+79991234562" not in active_phones


@pytest.mark.anyio
async def test_call_repo_get_by_vapi_id(session: AsyncSession):
    """get_by_vapi_id() finds a call by its Vapi-assigned ID."""
    repo = CallRepository(Call, session)
    call = Call(
        phone="+79991234567",
        mode=CallMode.VAPI,
        status=CallStatus.IN_PROGRESS,
        vapi_call_id="vapi-abc-123",
    )
    await repo.save(call)

    result = await repo.get_by_vapi_id("vapi-abc-123")
    assert result is not None
    assert result.vapi_call_id == "vapi-abc-123"


@pytest.mark.anyio
async def test_call_repo_get_by_vapi_id_missing(session: AsyncSession):
    """get_by_vapi_id() returns None for unknown ID."""
    repo = CallRepository(Call, session)
    result = await repo.get_by_vapi_id("nonexistent-vapi-id")
    assert result is None


# ── TranscriptRepository ──────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_transcript_append_and_get(session: AsyncSession):
    """append() adds entry; get_by_call() returns it in order."""
    call_repo = CallRepository(Call, session)
    call = Call(phone="+79991234567", mode=CallMode.AUTO, status=CallStatus.IN_PROGRESS)
    await call_repo.save(call)

    t_repo = TranscriptRepository(TranscriptEntry, session)
    await t_repo.append(call.id, TranscriptRole.ASSISTANT, "Hello there")
    await t_repo.append(call.id, TranscriptRole.USER, "Hi!")

    entries = await t_repo.get_by_call(call.id)
    assert len(entries) == 2
    assert entries[0].text == "Hello there"
    assert entries[0].role == TranscriptRole.ASSISTANT
    assert entries[1].text == "Hi!"
    assert entries[0].sequence_num < entries[1].sequence_num


@pytest.mark.anyio
async def test_transcript_sequence_nums_auto_assigned(session: AsyncSession):
    """sequence_num starts at 0 and increments by 1."""
    call_repo = CallRepository(Call, session)
    call = Call(phone="+79991234567", mode=CallMode.AUTO, status=CallStatus.IN_PROGRESS)
    await call_repo.save(call)

    t_repo = TranscriptRepository(TranscriptEntry, session)
    for i in range(5):
        await t_repo.append(call.id, TranscriptRole.ASSISTANT, f"line {i}")

    entries = await t_repo.get_by_call(call.id)
    seqs = [e.sequence_num for e in entries]
    assert seqs == list(range(5))


@pytest.mark.anyio
async def test_transcript_get_by_call_empty(session: AsyncSession):
    """get_by_call() returns empty list for call with no transcript."""
    call_repo = CallRepository(Call, session)
    call = Call(phone="+79991234567", mode=CallMode.AUTO, status=CallStatus.CREATED)
    await call_repo.save(call)

    t_repo = TranscriptRepository(TranscriptEntry, session)
    entries = await t_repo.get_by_call(call.id)
    assert entries == []


# ── BlockedPhoneRepository ────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_blocked_phone_lifecycle(session: AsyncSession):
    """Full block/check/unblock cycle."""
    repo = BlockedPhoneRepository(BlockedPhone, session)
    phone = "+79991230000"

    assert await repo.is_blocked(phone) is False

    await repo.block(phone, reason="test", added_by="admin")
    assert await repo.is_blocked(phone) is True

    entry = await repo.get_by_phone(phone)
    assert entry.reason == "test"
    assert entry.added_by == "admin"

    removed = await repo.unblock(phone)
    assert removed is True
    assert await repo.is_blocked(phone) is False

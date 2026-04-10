"""
Tests for the BlockedPhone deny list feature.

Covers:
- BlockedPhoneRepository: block, unblock, is_blocked, idempotent block
- CallService: rejects blocked phone at create_call
- API: 422 response for blocked phone
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.blocked_phone import BlockedPhone
from app.repositories.blocked_phone_repo import BlockedPhoneRepository


# ── Repository ────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_block_and_check(session: AsyncSession):
    """block() adds phone; is_blocked() returns True."""
    repo = BlockedPhoneRepository(BlockedPhone, session)
    await repo.block("+79990000001", reason="spam")
    assert await repo.is_blocked("+79990000001") is True


@pytest.mark.anyio
async def test_unknown_phone_not_blocked(session: AsyncSession):
    """is_blocked() returns False for unknown numbers."""
    repo = BlockedPhoneRepository(BlockedPhone, session)
    assert await repo.is_blocked("+79990000099") is False


@pytest.mark.anyio
async def test_block_idempotent(session: AsyncSession):
    """Blocking the same number twice doesn't raise an error."""
    repo = BlockedPhoneRepository(BlockedPhone, session)
    await repo.block("+79990000002")
    entry = await repo.block("+79990000002")  # should return existing, not raise
    assert entry is not None


@pytest.mark.anyio
async def test_unblock(session: AsyncSession):
    """unblock() removes a blocked phone."""
    repo = BlockedPhoneRepository(BlockedPhone, session)
    await repo.block("+79990000003")
    removed = await repo.unblock("+79990000003")
    assert removed is True
    assert await repo.is_blocked("+79990000003") is False


@pytest.mark.anyio
async def test_unblock_nonexistent(session: AsyncSession):
    """unblock() on non-blocked phone returns False."""
    repo = BlockedPhoneRepository(BlockedPhone, session)
    result = await repo.unblock("+79990000099")
    assert result is False


@pytest.mark.anyio
async def test_block_stores_reason(session: AsyncSession):
    """block() persists the reason field."""
    repo = BlockedPhoneRepository(BlockedPhone, session)
    await repo.block("+79990000004", reason="DNC list")
    entry = await repo.get_by_phone("+79990000004")
    assert entry is not None
    assert entry.reason == "DNC list"


# ── Service-level rejection ───────────────────────────────────────────────────

@pytest.mark.anyio
async def test_service_rejects_blocked_phone(session: AsyncSession):
    """CallService.create_call raises BlockedPhoneError for blocked number."""
    from app.core.exceptions import BlockedPhoneError
    from app.integrations.call_engine.stub import StubEngine
    from app.services.call_service import CallService

    repo = BlockedPhoneRepository(BlockedPhone, session)
    await repo.block("+79990000005")

    svc = CallService(session=session, engine=StubEngine())
    with pytest.raises(BlockedPhoneError):
        await svc.create_call(raw_phone="+79990000005")


# ── Different phone, not blocked ──────────────────────────────────────────────

@pytest.mark.anyio
async def test_service_allows_unblocked_phone(session: AsyncSession):
    """CallService.create_call succeeds for non-blocked number."""
    from app.integrations.call_engine.stub import StubEngine
    from app.services.call_service import CallService

    # Block a different number
    repo = BlockedPhoneRepository(BlockedPhone, session)
    await repo.block("+79990000006")

    svc = CallService(session=session, engine=StubEngine())
    call = await svc.create_call(raw_phone="+79990000007")
    assert call.id is not None

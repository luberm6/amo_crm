"""
Unit tests for CallService — business logic layer.

Tests cover:
- create_call: happy path, phone normalization, blocked phone, quiet hours
- steer_call: happy path, terminal call rejection
- stop_call: happy path, idempotency on terminal call
- audit trail: events created on create/steer/stop
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.call_engine.base import AbstractCallEngine, EngineCallResult
from app.models.call import Call, CallMode, CallStatus
from app.models.blocked_phone import BlockedPhone
from app.repositories.blocked_phone_repo import BlockedPhoneRepository
from app.services.call_service import CallService
from app.core.exceptions import (
    BlockedPhoneError,
    EngineError,
    InvalidCallStateError,
    NotFoundError,
    QuietHoursError,
)


# ── Stub engine helpers ───────────────────────────────────────────────────────

class _OkEngine(AbstractCallEngine):
    """Returns QUEUED status with a fixed external_id."""

    async def initiate_call(self, call: Call) -> EngineCallResult:
        return EngineCallResult(
            external_id="vapi-test-id",
            initial_status=CallStatus.QUEUED,
        )

    async def stop_call(self, call: Call) -> None:
        pass

    async def send_instruction(self, call: Call, instruction: str) -> None:
        self.last_instruction = instruction

    async def get_status(self, call: Call) -> CallStatus:
        return call.status


class _FailingEngine(AbstractCallEngine):
    async def initiate_call(self, call: Call) -> EngineCallResult:
        raise EngineError("media startup failed")

    async def stop_call(self, call: Call) -> None:
        pass

    async def send_instruction(self, call: Call, instruction: str) -> None:
        pass

    async def get_status(self, call: Call) -> CallStatus:
        return call.status


# ── create_call ───────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_create_call_happy_path(session: AsyncSession):
    """create_call with valid phone returns a Call in QUEUED status."""
    svc = CallService(session=session, engine=_OkEngine())
    call = await svc.create_call(raw_phone="+79991234567")

    assert call.id is not None
    assert call.phone == "+79991234567"
    assert call.status == CallStatus.QUEUED
    assert call.vapi_call_id == "vapi-test-id"
    assert call.mango_call_id is None


@pytest.mark.anyio
async def test_create_call_normalizes_phone(session: AsyncSession):
    """Phone number is normalized to E.164 before saving."""
    svc = CallService(session=session, engine=_OkEngine())
    # Russian local format
    call = await svc.create_call(raw_phone="89991234567")
    assert call.phone.startswith("+7")


@pytest.mark.anyio
async def test_create_call_blocked_phone(session: AsyncSession):
    """Blocked phone → BlockedPhoneError raised before call is created."""
    # Add to deny list
    repo = BlockedPhoneRepository(BlockedPhone, session)
    await repo.block("+79991234567", reason="test block")

    svc = CallService(session=session, engine=_OkEngine())
    with pytest.raises(BlockedPhoneError) as exc_info:
        await svc.create_call(raw_phone="+79991234567")

    assert exc_info.value.status_code == 422
    assert "deny list" in exc_info.value.message


@pytest.mark.anyio
async def test_create_call_writes_audit_events(session: AsyncSession):
    """create_call produces audit events: 'created' and 'status_changed'."""
    from sqlalchemy import select
    from app.models.audit import AuditEvent

    svc = CallService(session=session, engine=_OkEngine())
    call = await svc.create_call(raw_phone="+79991234567")

    result = await session.execute(
        select(AuditEvent).where(AuditEvent.entity_id == call.id)
    )
    events = result.scalars().all()
    actions = {e.action for e in events}

    assert "created" in actions
    assert "status_changed" in actions


@pytest.mark.anyio
async def test_create_call_engine_failure_marks_call_failed(session: AsyncSession):
    svc = CallService(session=session, engine=_FailingEngine())

    with pytest.raises(EngineError):
        await svc.create_call(raw_phone="+79991234567")

    from sqlalchemy import select

    result = await session.execute(select(Call).order_by(Call.created_at.desc()))
    call = result.scalars().first()
    assert call is not None
    assert call.status == CallStatus.FAILED
    assert call.completed_at is not None


@pytest.mark.anyio
async def test_create_call_quiet_hours_disabled_by_default(session: AsyncSession):
    """Quiet hours enforcement is disabled by default — call succeeds at any time."""
    svc = CallService(session=session, engine=_OkEngine())
    # Should not raise even at midnight
    call = await svc.create_call(raw_phone="+79991234567")
    assert call.id is not None


@pytest.mark.anyio
async def test_create_call_quiet_hours_raises_when_enforced(session: AsyncSession):
    """When enforce_quiet_hours=True and hour is outside window → QuietHoursError."""
    from app.core import config as cfg_module
    from unittest.mock import patch

    svc = CallService(session=session, engine=_OkEngine())

    # Patch settings to enforce quiet hours and simulate hour=3 (outside window)
    with patch.object(cfg_module.settings, "enforce_quiet_hours", True), \
         patch.object(cfg_module.settings, "calling_hour_start", 9), \
         patch.object(cfg_module.settings, "calling_hour_end", 21), \
         patch.object(cfg_module.settings, "calling_timezone", "UTC"):

        # Mock datetime to return hour=3
        import app.services.call_service as svc_module
        from datetime import datetime as _dt, timezone

        class _FakeDatetime:
            @staticmethod
            def now(tz=None):
                # Return 03:00 UTC
                return _dt(2026, 1, 1, 3, 0, 0, tzinfo=timezone.utc)

        with patch("app.services.call_service.datetime", _FakeDatetime):
            with pytest.raises(QuietHoursError) as exc_info:
                await svc.create_call(raw_phone="+79991234567")

    assert exc_info.value.status_code == 422


# ── steer_call ────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_steer_call_happy_path(session: AsyncSession):
    """steer_call on active call → SteeringInstruction saved."""
    engine = _OkEngine()
    svc = CallService(session=session, engine=engine)
    call = await svc.create_call(raw_phone="+79991234567")

    result = await svc.steer_call(
        call_id=call.id,
        instruction="Ask about the budget",
        issued_by="manager-1",
    )

    assert result.call_id == call.id
    assert result.instruction == "Ask about the budget"
    assert result.issued_by == "manager-1"
    assert engine.last_instruction == "Ask about the budget"


@pytest.mark.anyio
async def test_steer_terminal_call_raises(session: AsyncSession):
    """steer_call on COMPLETED call → InvalidCallStateError (422)."""
    svc = CallService(session=session, engine=_OkEngine())
    call = await svc.create_call(raw_phone="+79991234567")

    # Force terminal status
    call.status = CallStatus.COMPLETED
    await session.flush()

    with pytest.raises(InvalidCallStateError) as exc_info:
        await svc.steer_call(call_id=call.id, instruction="too late")

    assert exc_info.value.status_code == 422
    assert "terminal" in exc_info.value.message


@pytest.mark.anyio
async def test_steer_stopped_call_raises(session: AsyncSession):
    """steer_call on STOPPED call → InvalidCallStateError."""
    svc = CallService(session=session, engine=_OkEngine())
    call = await svc.create_call(raw_phone="+79991234567")
    call.status = CallStatus.STOPPED
    await session.flush()

    with pytest.raises(InvalidCallStateError):
        await svc.steer_call(call_id=call.id, instruction="test")


@pytest.mark.anyio
async def test_steer_not_found_raises(session: AsyncSession):
    """steer_call with unknown call_id → NotFoundError."""
    svc = CallService(session=session, engine=_OkEngine())
    with pytest.raises(NotFoundError):
        await svc.steer_call(call_id=uuid.uuid4(), instruction="test")


# ── stop_call ─────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_stop_call_happy_path(session: AsyncSession):
    """stop_call on active call → status becomes STOPPED."""
    svc = CallService(session=session, engine=_OkEngine())
    call = await svc.create_call(raw_phone="+79991234567")
    assert call.is_active()

    stopped = await svc.stop_call(call_id=call.id, actor="test")
    assert stopped.status == CallStatus.STOPPED
    assert stopped.completed_at is not None


@pytest.mark.anyio
async def test_stop_call_idempotent_for_terminal(session: AsyncSession):
    """stop_call on already-completed call → returns call unchanged."""
    svc = CallService(session=session, engine=_OkEngine())
    call = await svc.create_call(raw_phone="+79991234567")
    call.status = CallStatus.COMPLETED
    await session.flush()

    result = await svc.stop_call(call_id=call.id)
    assert result.status == CallStatus.COMPLETED  # not changed to STOPPED


@pytest.mark.anyio
async def test_stop_call_not_found_raises(session: AsyncSession):
    """stop_call with unknown call_id → NotFoundError."""
    svc = CallService(session=session, engine=_OkEngine())
    with pytest.raises(NotFoundError):
        await svc.stop_call(call_id=uuid.uuid4())


@pytest.mark.anyio
async def test_stop_call_writes_audit_event(session: AsyncSession):
    """stop_call writes a 'stopped' audit event."""
    from sqlalchemy import select
    from app.models.audit import AuditEvent

    svc = CallService(session=session, engine=_OkEngine())
    call = await svc.create_call(raw_phone="+79991234567")
    await svc.stop_call(call_id=call.id, actor="bot")

    result = await session.execute(
        select(AuditEvent)
        .where(AuditEvent.entity_id == call.id, AuditEvent.action == "stopped")
    )
    events = result.scalars().all()
    assert len(events) == 1
    assert events[0].actor == "bot"

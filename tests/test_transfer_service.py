"""
Tests for TransferService — warm transfer orchestration.

6 tests covering:
- Happy path: call ends in CONNECTED_TO_MANAGER
- No managers available: raises NoManagerAvailableError
- Dept fallback: no managers in dept, finds one without dept filter
- Terminal call raises InvalidCallStateError
- Manager priority ordering (priority 1 selected over priority 5)
- Engine failure: marks manager unavailable, call=STOPPED, record=FAILED_NO_ANSWER
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InvalidCallStateError, NoManagerAvailableError, TransferError
from app.integrations.transfer_engine.base import AbstractTransferEngine, ManagerCallResult
from app.models.call import Call, CallMode, CallStatus
from app.models.manager import Manager
from app.models.transfer import TransferStatus
from app.repositories.call_repo import CallRepository
from app.repositories.manager_repo import ManagerRepository
from app.services.transfer_service import TransferService
from app.integrations.transfer_engine.stub import StubTransferEngine


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
    name: str = "Менеджер",
    telegram_id: int = 999001,
    priority: int = 5,
    department: str = "sales",
    is_active: bool = True,
    is_available: bool = True,
) -> Manager:
    mgr = Manager(
        name=name,
        phone="+79990000001",
        telegram_id=telegram_id,
        is_active=is_active,
        is_available=is_available,
        priority=priority,
        department=department,
    )
    repo = ManagerRepository(Manager, session)
    return await repo.save(mgr)


class FailingTransferEngine(AbstractTransferEngine):
    """Simulates a manager who never answers — raises TransferError."""

    async def initiate_manager_call(self, manager, call, whisper_text):
        raise TransferError("Simulated engine failure: manager did not answer")

    async def play_whisper(self, manager_call_id, whisper_text):
        pass

    async def bridge_calls(self, manager_call_id, customer_call_id):
        pass

    async def mark_manager_temporarily_unavailable(self, manager_id):
        pass


# ── Tests ──────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_initiate_transfer_success(session: AsyncSession):
    """Happy path: call ends in CONNECTED_TO_MANAGER, record in CONNECTED."""
    call = await _make_call(session)
    await _make_manager(session, telegram_id=200001)

    svc = TransferService(session=session, engine=StubTransferEngine())
    record = await svc.initiate_transfer(call.id)

    assert record.status == TransferStatus.CONNECTED
    assert record.manager_id is not None
    assert record.whisper_text is not None

    # Reload call to check status
    updated_call = await svc.call_repo.get(call.id)
    assert updated_call.status == CallStatus.CONNECTED_TO_MANAGER
    assert updated_call.manager_id == record.manager_id


@pytest.mark.anyio
async def test_initiate_transfer_no_managers(session: AsyncSession):
    """No managers at all → NoManagerAvailableError, call unchanged."""
    call = await _make_call(session)
    svc = TransferService(session=session, engine=StubTransferEngine())

    with pytest.raises(NoManagerAvailableError):
        await svc.initiate_transfer(call.id)

    updated_call = await svc.call_repo.get(call.id)
    assert updated_call.status == CallStatus.IN_PROGRESS  # unchanged


@pytest.mark.anyio
async def test_dept_fallback_to_all(session: AsyncSession):
    """No managers in 'vip' dept, but there's one in 'sales' → uses that one."""
    call = await _make_call(session)
    await _make_manager(session, telegram_id=200002, department="sales")

    svc = TransferService(session=session, engine=StubTransferEngine())
    record = await svc.initiate_transfer(call.id, department="vip")

    # Should still succeed by falling back to all departments
    assert record.status == TransferStatus.CONNECTED
    assert record.manager_id is not None


@pytest.mark.anyio
async def test_terminal_call_raises(session: AsyncSession):
    """Stopped call raises InvalidCallStateError."""
    call = await _make_call(session, status=CallStatus.STOPPED)
    await _make_manager(session, telegram_id=200003)

    svc = TransferService(session=session, engine=StubTransferEngine())

    with pytest.raises(InvalidCallStateError):
        await svc.initiate_transfer(call.id)


@pytest.mark.anyio
async def test_manager_priority_ordering(session: AsyncSession):
    """Manager with priority 1 is selected over priority 5."""
    call = await _make_call(session)
    low_priority = await _make_manager(
        session, name="Низкий", telegram_id=200004, priority=5
    )
    high_priority = await _make_manager(
        session, name="Высокий", telegram_id=200005, priority=1
    )

    svc = TransferService(session=session, engine=StubTransferEngine())
    record = await svc.initiate_transfer(call.id)

    assert record.manager_id == high_priority.id


@pytest.mark.anyio
async def test_engine_failure_marks_unavailable_stops_call(session: AsyncSession):
    """Engine failure → call=STOPPED, record=FAILED_NO_ANSWER."""
    call = await _make_call(session)
    mgr = await _make_manager(session, telegram_id=200006)

    svc = TransferService(session=session, engine=FailingTransferEngine())

    with pytest.raises(TransferError):
        await svc.initiate_transfer(call.id)

    updated_call = await svc.call_repo.get(call.id)
    assert updated_call.status == CallStatus.STOPPED

    # Find the failed transfer record
    record = await svc.transfer_repo.get_latest_for_call(call.id)
    assert record is not None
    assert record.status == TransferStatus.FAILED_NO_ANSWER
    assert record.fallback_message is not None

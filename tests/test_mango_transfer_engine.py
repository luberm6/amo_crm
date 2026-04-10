from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import CallerDroppedError, TransferError
from app.core.config import settings
from app.integrations.telephony.base import TelephonyLegState, TelephonyOriginateResult
from app.integrations.transfer_engine.mango import MangoTransferEngine
from app.models.call import Call, CallMode, CallStatus
from app.models.manager import Manager
from app.models.transfer import TransferStatus
from app.repositories.call_repo import CallRepository
from app.repositories.manager_repo import ManagerRepository
from app.services.transfer_service import TransferService


class FakeMangoTelephony:
    def __init__(self) -> None:
        self._originate_count = 0
        self._next_wait_error: dict[str, Exception] = {}
        self.terminated_legs: list[str] = []
        self.bridged: list[tuple[str, str]] = []
        self.whispers: list[str] = []

    async def originate_call(self, phone: str, caller_id=None, metadata=None):
        self._originate_count += 1
        return TelephonyOriginateResult(leg_id=f"mgr-leg-{self._originate_count}")

    async def wait_for_answered(self, leg_id: str, timeout: float):
        err = self._next_wait_error.get(leg_id)
        if err:
            raise err
        return TelephonyLegState.ANSWERED

    async def play_whisper(self, leg_id: str, message: str):
        self.whispers.append(leg_id)
        return None

    async def bridge_legs(self, customer_leg_id: str, manager_leg_id: str):
        self.bridged.append((customer_leg_id, manager_leg_id))
        return None

    async def terminate_leg(self, leg_id: str):
        self.terminated_legs.append(leg_id)
        return None

    async def get_leg_state(self, leg_id: str):
        return TelephonyLegState.ANSWERED


async def _make_call(session: AsyncSession, phone: str = "+79991234567") -> Call:
    call = Call(
        phone=phone,
        mode=CallMode.DIRECT,
        status=CallStatus.IN_PROGRESS,
        telephony_leg_id=f"cust-leg-{uuid.uuid4().hex[:8]}",
    )
    return await CallRepository(Call, session).save(call)


async def _make_manager(
    session: AsyncSession,
    *,
    name: str,
    telegram_id: int,
    priority: int,
    phone: str,
    department: str = "sales",
) -> Manager:
    manager = Manager(
        name=name,
        phone=phone,
        telegram_id=telegram_id,
        priority=priority,
        is_active=True,
        is_available=True,
        department=department,
    )
    return await ManagerRepository(Manager, session).save(manager)


@pytest.mark.anyio
async def test_mango_transfer_engine_progress_success(session: AsyncSession, test_engine):
    telephony = FakeMangoTelephony()
    factory = async_sessionmaker(bind=test_engine, expire_on_commit=False)
    engine = MangoTransferEngine(telephony=telephony, session_factory=factory, direct_session_manager=None)
    manager = await _make_manager(
        session,
        name="A",
        telegram_id=410001,
        priority=1,
        phone="+79990000001",
    )
    call = await _make_call(session)

    result = await engine.initiate_manager_call(manager, call, whisper_text="x")
    await engine.play_whisper(result.external_id, "brief")
    await engine.bridge_calls(result.external_id, call.telephony_leg_id)

    progress = await engine.get_transfer_progress(result.external_id)
    assert progress is not None
    assert progress["status"] == "bridged"
    assert telephony.whispers == [result.external_id]
    assert telephony.bridged == [(call.telephony_leg_id, result.external_id)]


@pytest.mark.anyio
async def test_mango_transfer_retry_next_manager(session: AsyncSession, test_engine):
    telephony = FakeMangoTelephony()
    factory = async_sessionmaker(bind=test_engine, expire_on_commit=False)
    engine = MangoTransferEngine(telephony=telephony, session_factory=factory, direct_session_manager=None)
    svc = TransferService(session=session, engine=engine)

    call = await _make_call(session)
    await _make_manager(
        session,
        name="First",
        telegram_id=410010,
        priority=1,
        phone="+79990000101",
    )
    await _make_manager(
        session,
        name="Second",
        telegram_id=410011,
        priority=2,
        phone="+79990000102",
    )
    await session.flush()

    # Fail first manager answer, second should be used.
    telephony._next_wait_error["mgr-leg-1"] = TransferError("no answer")

    record = await svc.initiate_transfer(call.id)
    assert record.status == TransferStatus.CONNECTED
    assert record.attempt_count == 2


@pytest.mark.anyio
async def test_mango_transfer_caller_dropped_during_whisper_cleanup(
    session: AsyncSession, test_engine
):
    telephony = FakeMangoTelephony()
    factory = async_sessionmaker(bind=test_engine, expire_on_commit=False)
    engine = MangoTransferEngine(telephony=telephony, session_factory=factory, direct_session_manager=None)
    svc = TransferService(session=session, engine=engine)

    call = await _make_call(session)
    await _make_manager(
        session,
        name="A",
        telegram_id=410020,
        priority=1,
        phone="+79990000201",
    )

    # call_is_terminal checks: before dial=False, after answer=False, before bridge=True.
    svc._call_is_terminal = AsyncMock(side_effect=[False, False, True])  # type: ignore[attr-defined]

    with pytest.raises(CallerDroppedError):
        await svc.initiate_transfer(call.id)

    assert telephony.terminated_legs, "Manager leg must be cleaned up when caller drops"


@pytest.mark.anyio
async def test_mango_transfer_concurrency_single_manager_reservation(
    session: AsyncSession, test_engine
):
    telephony = FakeMangoTelephony()
    factory = async_sessionmaker(bind=test_engine, expire_on_commit=False)
    engine = MangoTransferEngine(telephony=telephony, session_factory=factory, direct_session_manager=None)

    call1 = await _make_call(session, phone="+79991230001")
    call2 = await _make_call(session, phone="+79991230002")
    dept = "concurrency_only_one"
    await _make_manager(
        session,
        name="OnlyOne",
        telegram_id=410030,
        priority=1,
        phone="+79990000301",
        department=dept,
    )

    # Two independent service instances with isolated SQLAlchemy sessions.
    s_factory = async_sessionmaker(bind=test_engine, expire_on_commit=False)
    async with s_factory() as s1, s_factory() as s2:
        svc1 = TransferService(session=s1, engine=engine)
        svc2 = TransferService(session=s2, engine=engine)

        res = await asyncio.gather(
            svc1.initiate_transfer(call1.id, department=dept),
            svc2.initiate_transfer(call2.id, department=dept),
            return_exceptions=True,
        )

    successes = [x for x in res if not isinstance(x, Exception)]
    failures = [x for x in res if isinstance(x, Exception)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], TransferError)


@pytest.mark.anyio
async def test_bridge_suspends_direct_audio_session_when_mapped(session: AsyncSession, test_engine):
    telephony = FakeMangoTelephony()
    factory = async_sessionmaker(bind=test_engine, expire_on_commit=False)

    class _SM:
        def __init__(self):
            self.calls = []

        async def suspend_audio(self, session_id: str, reason: str = "transfer"):
            self.calls.append((session_id, reason))

    sm = _SM()
    engine = MangoTransferEngine(
        telephony=telephony,
        session_factory=factory,
        direct_session_manager=sm,
    )
    manager = await _make_manager(
        session,
        name="A",
        telegram_id=410050,
        priority=1,
        phone="+79990000501",
    )
    call = await _make_call(session)
    call.mango_call_id = "session-abc-direct"
    await CallRepository(Call, session).save(call)

    res = await engine.initiate_manager_call(manager, call, whisper_text="w")
    await engine.bridge_calls(res.external_id, call.telephony_leg_id)
    assert sm.calls == [("session-abc-direct", "warm_transfer_bridged")]


@pytest.mark.anyio
async def test_mark_manager_temporarily_unavailable_restores_after_cooldown(
    session: AsyncSession, test_engine
):
    telephony = FakeMangoTelephony()
    factory = async_sessionmaker(bind=test_engine, expire_on_commit=False)
    engine = MangoTransferEngine(
        telephony=telephony,
        session_factory=factory,
        direct_session_manager=None,
    )
    manager = await _make_manager(
        session,
        name="Cooldown",
        telegram_id=410060,
        priority=1,
        phone="+79990000601",
    )

    old_cd = settings.transfer_manager_cooldown_seconds
    try:
        settings.transfer_manager_cooldown_seconds = 0
        await engine.mark_manager_temporarily_unavailable(manager.id)
        await asyncio.sleep(0.02)
        await session.refresh(manager)
        assert manager.is_available is True
    finally:
        settings.transfer_manager_cooldown_seconds = old_cd
        # Cleanup committed row from secondary session used by restore task.
        cleanup_factory = async_sessionmaker(bind=test_engine, expire_on_commit=False)
        async with cleanup_factory() as cleanup_session:
            row = await ManagerRepository(Manager, cleanup_session).get(manager.id)
            if row is not None:
                await cleanup_session.delete(row)
                await cleanup_session.commit()

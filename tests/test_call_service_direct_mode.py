"""
Тесты для CallService routing/persistence — проверка куда пишется external_id.

Покрываем критичный случай:
- mode может быть AUTO, но фактический route_used == "direct"
- stop/steer дальше должны видеть session_id в mango_call_id
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.call_engine.base import AbstractCallEngine, EngineCallResult
from app.models.call import Call, CallMode, CallStatus
from app.services.call_service import CallService


class _StubEngineWithId(AbstractCallEngine):
    """Stub engine возвращающий фиксированный external_id."""

    def __init__(
        self,
        ext_id: str = "test-external-id",
        *,
        route_used: str = "vapi",
    ) -> None:
        self.ext_id = ext_id
        self.route_used = route_used
        self.stopped_call_ids: list[str] = []
        self.stopped_session_ids: list[str | None] = []
        self.instructions: list[tuple[str, str | None, str]] = []

    async def initiate_call(self, call: Call) -> EngineCallResult:
        return EngineCallResult(
            external_id=self.ext_id,
            initial_status=CallStatus.IN_PROGRESS,
            route_used=self.route_used,
        )

    async def stop_call(self, call: Call) -> None:
        self.stopped_call_ids.append(str(call.id))
        self.stopped_session_ids.append(call.mango_call_id)

    async def send_instruction(self, call: Call, instruction: str) -> None:
        self.instructions.append((str(call.id), call.mango_call_id, instruction))

    async def get_status(self, call: Call) -> CallStatus:
        return call.status


@pytest.mark.anyio
async def test_direct_mode_writes_mango_call_id(session: AsyncSession):
    """create_call(mode=DIRECT) → external_id пишется в mango_call_id."""
    engine = _StubEngineWithId(
        "direct-session-uuid-direct",
        route_used="direct",
    )
    svc = CallService(session=session, engine=engine)

    call = await svc.create_call(raw_phone="+79991234567", mode=CallMode.DIRECT)

    assert call.mango_call_id == "direct-session-uuid-direct"
    assert call.vapi_call_id is None


@pytest.mark.anyio
async def test_vapi_mode_writes_vapi_call_id(session: AsyncSession):
    """create_call(mode=VAPI) → external_id пишется в vapi_call_id."""
    engine = _StubEngineWithId(
        "vapi-call-id-from-engine",
        route_used="vapi",
    )
    svc = CallService(session=session, engine=engine)

    call = await svc.create_call(raw_phone="+79991234567", mode=CallMode.VAPI)

    assert call.vapi_call_id == "vapi-call-id-from-engine"
    assert call.mango_call_id is None


@pytest.mark.anyio
async def test_auto_mode_with_vapi_route_writes_vapi_call_id(session: AsyncSession):
    """create_call(mode=AUTO, route=vapi) → external_id пишется в vapi_call_id."""
    engine = _StubEngineWithId("auto-vapi-ext-id", route_used="vapi")
    svc = CallService(session=session, engine=engine)

    call = await svc.create_call(raw_phone="+79991234567", mode=CallMode.AUTO)

    assert call.route_used == "vapi"
    assert call.vapi_call_id == "auto-vapi-ext-id"
    assert call.mango_call_id is None


@pytest.mark.anyio
async def test_auto_mode_with_direct_route_writes_mango_call_id(session: AsyncSession):
    """create_call(mode=AUTO, route=direct) сохраняет Direct session в mango_call_id."""
    engine = _StubEngineWithId("auto-direct-session-id", route_used="direct")
    svc = CallService(session=session, engine=engine)

    call = await svc.create_call(raw_phone="+79991234567", mode=CallMode.AUTO)

    assert call.mode == CallMode.AUTO
    assert call.route_used == "direct"
    assert call.mango_call_id == "auto-direct-session-id"
    assert call.vapi_call_id is None


@pytest.mark.anyio
async def test_auto_direct_call_stop_uses_persisted_direct_session_id(session: AsyncSession):
    """AUTO→direct: stop_call должен видеть сохранённый mango_call_id."""
    engine = _StubEngineWithId("auto-direct-stop-session", route_used="direct")
    svc = CallService(session=session, engine=engine)

    call = await svc.create_call(raw_phone="+79991234567", mode=CallMode.AUTO)
    stopped = await svc.stop_call(call.id)

    assert stopped.status == CallStatus.STOPPED
    assert engine.stopped_call_ids == [str(call.id)]
    assert engine.stopped_session_ids == ["auto-direct-stop-session"]


@pytest.mark.anyio
async def test_auto_direct_call_steer_uses_persisted_direct_session_id(session: AsyncSession):
    """AUTO→direct: steer_call должен отправлять instruction в Direct session."""
    engine = _StubEngineWithId("auto-direct-steer-session", route_used="direct")
    svc = CallService(session=session, engine=engine)

    call = await svc.create_call(raw_phone="+79991234567", mode=CallMode.AUTO)
    steering = await svc.steer_call(
        call.id,
        instruction="Уточни бюджет",
        issued_by="tester",
    )

    assert steering.instruction == "Уточни бюджет"
    assert engine.instructions == [
        (str(call.id), "auto-direct-steer-session", "Уточни бюджет")
    ]

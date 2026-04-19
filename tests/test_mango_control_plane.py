from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.integrations.telephony.base import TelephonyLegState
from app.integrations.telephony.mango import MangoTelephonyAdapter, TelephonyError
from app.integrations.telephony.mango_events import MangoEventProcessor
from app.integrations.telephony.mango_freeswitch_correlation import (
    InMemoryMangoFreeSwitchCorrelationStore,
)
from app.integrations.telephony.mango_state_store import InMemoryMangoLegStateStore
from app.models.call import Call, CallMode, CallStatus
from app.models.transfer import TransferRecord, TransferStatus
from app.repositories.call_repo import CallRepository
from app.repositories.transfer_repo import TransferRepository


def _make_adapter(
    store: InMemoryMangoLegStateStore,
    corr: InMemoryMangoFreeSwitchCorrelationStore | None = None,
) -> MangoTelephonyAdapter:
    adapter = MangoTelephonyAdapter.__new__(MangoTelephonyAdapter)
    adapter._api_key = "k"
    adapter._api_salt = "s"
    adapter._from_ext = "101"
    adapter._state = store
    adapter._corr = corr or InMemoryMangoFreeSwitchCorrelationStore()
    adapter._http = AsyncMock()
    return adapter


@pytest.mark.anyio
async def test_mango_event_normalization_and_processing(session: AsyncSession):
    store = InMemoryMangoLegStateStore()
    processor = MangoEventProcessor(session=session, store=store)

    call = Call(
        phone="+79990000001",
        mode=CallMode.DIRECT,
        status=CallStatus.DIALING,
        telephony_leg_id="leg-100",
    )
    call = await CallRepository(Call, session).save(call)

    event = await processor.process(
        {
            "event_id": "evt-1",
            "event": "ringing",
            "call_id": "leg-100",
            "internal_call_id": str(call.id),
            "leg_role": "customer",
        }
    )

    assert event.state == TelephonyLegState.RINGING
    snap = await store.get_leg_state("leg-100")
    assert snap is not None
    assert snap.state == TelephonyLegState.RINGING
    assert snap.call_id == str(call.id)


@pytest.mark.anyio
async def test_mango_event_command_id_alias_updates_provisional_leg(session: AsyncSession):
    store = InMemoryMangoLegStateStore()
    corr = InMemoryMangoFreeSwitchCorrelationStore()
    processor = MangoEventProcessor(session=session, store=store, correlation_store=corr)

    call = Call(
        phone="+79990000003",
        mode=CallMode.DIRECT,
        status=CallStatus.DIALING,
        telephony_leg_id="direct-cmd-123",
    )
    call = await CallRepository(Call, session).save(call)

    event = await processor.process(
        {
            "event_id": "evt-cmd-1",
            "event": "answered",
            "command_id": "direct-cmd-123",
            "call_id": "leg-real-777",
        }
    )

    assert event.command_id == "direct-cmd-123"
    real_snap = await store.get_leg_state("leg-real-777")
    alias_snap = await store.get_leg_state("direct-cmd-123")
    assert real_snap is not None
    assert real_snap.state == TelephonyLegState.ANSWERED
    assert alias_snap is not None
    assert alias_snap.state == TelephonyLegState.ANSWERED
    assert alias_snap.call_id == str(call.id)


@pytest.mark.anyio
async def test_wait_for_answered_webhook_first():
    store = InMemoryMangoLegStateStore()
    adapter = _make_adapter(store)

    async def emit_answer():
        await asyncio.sleep(0.05)
        await store.set_leg_state("leg-200", TelephonyLegState.ANSWERED)

    task = asyncio.create_task(emit_answer())
    state = await adapter.wait_for_answered("leg-200", timeout=1.0)
    await task
    assert state == TelephonyLegState.ANSWERED


@pytest.mark.anyio
async def test_wait_for_answered_does_not_poll_stats_endpoint():
    store = InMemoryMangoLegStateStore()
    adapter = _make_adapter(store)
    adapter._http.get = AsyncMock(side_effect=AssertionError("stats polling must stay disabled"))

    async def emit_answer():
        await asyncio.sleep(0.05)
        await store.set_leg_state("leg-300", TelephonyLegState.ANSWERED)

    task = asyncio.create_task(emit_answer())
    state = await adapter.wait_for_answered("leg-300", timeout=1.0)
    await task
    assert state == TelephonyLegState.ANSWERED
    adapter._http.get.assert_not_awaited()


@pytest.mark.anyio
async def test_wait_for_answered_uses_freeswitch_correlation_before_polling():
    store = InMemoryMangoLegStateStore()
    corr = InMemoryMangoFreeSwitchCorrelationStore()
    adapter = _make_adapter(store, corr)

    async def emit_fs_answer():
        await asyncio.sleep(0.05)
        await corr.set_freeswitch_state(
            mango_leg_id="leg-fs-ans",
            state=TelephonyLegState.ANSWERED,
            freeswitch_uuid="fs-uuid-1",
            freeswitch_session_id="fs-session-1",
        )

    task = asyncio.create_task(emit_fs_answer())
    state = await adapter.wait_for_answered("leg-fs-ans", timeout=1.0)
    await task
    assert state == TelephonyLegState.ANSWERED


@pytest.mark.anyio
async def test_wait_for_answered_hangup_before_answer_from_freeswitch():
    store = InMemoryMangoLegStateStore()
    corr = InMemoryMangoFreeSwitchCorrelationStore()
    adapter = _make_adapter(store, corr)

    async def emit_fs_hangup():
        await asyncio.sleep(0.05)
        await corr.set_freeswitch_state(
            mango_leg_id="leg-fs-hup",
            state=TelephonyLegState.TERMINATED,
            freeswitch_uuid="fs-uuid-2",
            freeswitch_session_id="fs-session-2",
        )

    task = asyncio.create_task(emit_fs_hangup())
    with pytest.raises(TelephonyError):
        await adapter.wait_for_answered("leg-fs-hup", timeout=1.0)
    await task


@pytest.mark.anyio
async def test_freeswitch_duplicate_answer_events_are_idempotent():
    corr = InMemoryMangoFreeSwitchCorrelationStore()
    await corr.set_freeswitch_state(
        mango_leg_id="leg-fs-dup",
        state=TelephonyLegState.ANSWERED,
        freeswitch_uuid="fs-dup-1",
        freeswitch_session_id="fs-s-dup",
    )
    await corr.set_freeswitch_state(
        mango_leg_id="leg-fs-dup",
        state=TelephonyLegState.ANSWERED,
        freeswitch_uuid="fs-dup-1",
        freeswitch_session_id="fs-s-dup",
    )
    snap = await corr.get("leg-fs-dup")
    assert snap is not None
    assert snap.effective_state == TelephonyLegState.ANSWERED


@pytest.mark.anyio
async def test_stale_mango_state_recovered_from_freeswitch_correlation():
    store = InMemoryMangoLegStateStore()
    corr = InMemoryMangoFreeSwitchCorrelationStore()
    adapter = _make_adapter(store, corr)
    await corr.set_freeswitch_state(
        mango_leg_id="leg-stale",
        state=TelephonyLegState.ANSWERED,
        freeswitch_uuid="fs-stale-1",
        freeswitch_session_id="fs-s-stale",
    )
    state = await adapter.get_leg_state("leg-stale")
    assert state == TelephonyLegState.ANSWERED


@pytest.mark.anyio
async def test_duplicate_mango_events_deduplicated(session: AsyncSession):
    store = InMemoryMangoLegStateStore()
    processor = MangoEventProcessor(session=session, store=store)

    call = Call(
        phone="+79990000002",
        mode=CallMode.DIRECT,
        status=CallStatus.IN_PROGRESS,
        telephony_leg_id="leg-dup",
    )
    call = await CallRepository(Call, session).save(call)
    transfer = TransferRecord(
        call_id=call.id,
        status=TransferStatus.BRIEFING,
        manager_call_id="leg-dup",
    )
    transfer = await TransferRepository(TransferRecord, session).save(transfer)

    payload = {
        "event_id": "evt-dup-1",
        "event": "whisper_failed",
        "call_id": "leg-dup",
        "internal_call_id": str(call.id),
        "transfer_id": str(transfer.id),
    }
    await processor.process(payload)
    await processor.process(payload)

    assert len(store._seen_events) == 1
    updated = await TransferRepository(TransferRecord, session).get(transfer.id)
    assert updated.status == TransferStatus.BRIDGE_FAILED


@pytest.mark.anyio
async def test_restart_state_restore_in_memory_shared_backing():
    shared_legs: dict[str, dict] = {}
    shared_bridge: dict[str, str] = {}
    shared_whisper: dict[str, str] = {}
    shared_seen: set[str] = set()

    store_before = InMemoryMangoLegStateStore(
        legs=shared_legs,
        bridge_ops=shared_bridge,
        whisper_ops=shared_whisper,
        seen_events=shared_seen,
    )
    await store_before.set_leg_state(
        "leg-restart",
        TelephonyLegState.ANSWERED,
        call_id=str(uuid.uuid4()),
    )

    # Simulate process restart: new store instance with same persistent backing.
    store_after = InMemoryMangoLegStateStore(
        legs=shared_legs,
        bridge_ops=shared_bridge,
        whisper_ops=shared_whisper,
        seen_events=shared_seen,
    )
    snap = await store_after.get_leg_state("leg-restart")
    assert snap is not None
    assert snap.state == TelephonyLegState.ANSWERED


@pytest.mark.anyio
async def test_mango_webhook_endpoint_shared_secret_guard(client: AsyncClient):
    old_secret = settings.mango_webhook_shared_secret
    try:
        settings.mango_webhook_shared_secret = "test-secret"
        payload = {"event_id": "evt-endpoint-1", "event": "ringing", "call_id": "leg-500"}

        denied = await client.post("/v1/webhooks/mango", json=payload)
        assert denied.status_code == 401

        allowed = await client.post(
            "/v1/webhooks/mango",
            json=payload,
            headers={"x-mango-webhook-secret": "test-secret"},
        )
        assert allowed.status_code == 200
        assert allowed.json()["status"] == "ok"
    finally:
        settings.mango_webhook_shared_secret = old_secret

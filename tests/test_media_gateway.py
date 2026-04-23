from __future__ import annotations

import asyncio
import contextlib
import socket
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.integrations.media_gateway.base import MediaEventType, MediaGatewayNotReadyError
from app.integrations.media_gateway.freeswitch import (
    FreeSwitchGatewayConfig,
    FreeSwitchMediaGateway,
    _build_rtp_packet,
    _encode_outbound_audio,
    _extract_rtp_payload,
    _decode_inbound_audio,
)
from app.integrations.telephony.base import TelephonyChannel, TelephonyLegState
from app.integrations.telephony.freeswitch_bridge import FreeSwitchAudioBridge
from app.integrations.telephony.mango import MangoTelephonyAdapter
from app.integrations.telephony.mango_freeswitch_correlation import (
    CorrelatedLegSnapshot,
)


@pytest.mark.anyio
async def test_freeswitch_media_gateway_mock_contract_roundtrip():
    gw = FreeSwitchMediaGateway(FreeSwitchGatewayConfig(mode="mock"))
    handle = await gw.attach_session(call_id="call-1", provider_leg_id="leg-1")

    await gw.inject_inbound_audio(handle.session_id, b"\x01" * 640)
    await gw.send_barge_in(handle.session_id)
    await gw.send_audio(handle.session_id, b"\x02" * 320)
    await gw.propagate_hangup(handle.session_id, reason="caller_hangup")

    events = []
    async for evt in gw.events(handle.session_id):
        events.append(evt.type)

    assert events == [MediaEventType.AUDIO_IN, MediaEventType.BARGE_IN, MediaEventType.HANGUP]
    assert gw.get_audio_out_bytes(handle.session_id) == 320


@pytest.mark.anyio
async def test_freeswitch_media_gateway_scaffold_explicit_not_ready():
    gw = FreeSwitchMediaGateway(FreeSwitchGatewayConfig(mode="scaffold"))
    with pytest.raises(MediaGatewayNotReadyError):
        await gw.attach_session(call_id="call-2", provider_leg_id="leg-2")


@pytest.mark.anyio
async def test_freeswitch_audio_bridge_consumes_events_and_stops_on_hangup():
    gw = FreeSwitchMediaGateway(FreeSwitchGatewayConfig(mode="mock"))
    bridge = FreeSwitchAudioBridge(gateway=gw)
    channel = TelephonyChannel(
        channel_id="c-1",
        phone="+79990001122",
        provider_leg_id="leg-100",
        state=TelephonyLegState.INITIATING,
    )
    await bridge.open(channel)
    assert bridge.is_open is True

    session_id = bridge._session_id  # test introspection
    assert session_id is not None
    await gw.inject_inbound_audio(session_id, b"\x03" * 640)
    await gw.send_barge_in(session_id)
    await gw.propagate_hangup(session_id, reason="hangup_test")

    chunks = []
    async for pcm in bridge.audio_in():
        chunks.append(pcm)

    assert len(chunks) == 1
    assert chunks[0] == b"\x03" * 640
    assert bridge.barge_in_triggered is True
    assert bridge.hangup_reason == "hangup_test"


@pytest.mark.anyio
async def test_mango_attach_audio_bridge_uses_media_gateway_mock_mode():
    from app.integrations.media_gateway import factory as mg_factory

    old_enabled = settings.media_gateway_enabled
    old_mode = settings.media_gateway_mode
    old_provider = settings.media_gateway_provider
    prev_gateway = mg_factory._gateway
    try:
        settings.media_gateway_enabled = True
        settings.media_gateway_mode = "mock"
        settings.media_gateway_provider = "freeswitch"
        mg_factory._gateway = None

        adapter = MangoTelephonyAdapter()
        channel = TelephonyChannel(
            channel_id="c-2",
            phone="+79990001123",
            provider_leg_id="leg-101",
            state=TelephonyLegState.INITIATING,
        )
        bridge = await adapter.attach_audio_bridge(channel)
        assert bridge.is_open is True
        await adapter.detach_audio_bridge(bridge)
    finally:
        settings.media_gateway_enabled = old_enabled
        settings.media_gateway_mode = old_mode
        settings.media_gateway_provider = old_provider
        mg_factory._gateway = prev_gateway


@pytest.mark.anyio
async def test_freeswitch_bridge_prefers_correlated_real_uuid_when_available():
    from app.integrations.telephony import freeswitch_bridge as bridge_module

    class _CaptureGateway:
        def __init__(self):
            self.calls = []

        async def attach_session(self, *, call_id: str, provider_leg_id: str, metadata=None):
            self.calls.append((call_id, provider_leg_id, metadata))
            from app.integrations.media_gateway.base import MediaSessionHandle

            return MediaSessionHandle(
                session_id="fs-session-1",
                call_id=call_id,
                provider_leg_id=provider_leg_id,
                metadata=metadata or {},
            )

        async def detach_session(self, session_id: str) -> None:
            return None

    gateway = _CaptureGateway()
    bridge = FreeSwitchAudioBridge(gateway=gateway)  # type: ignore[arg-type]
    channel = TelephonyChannel(
        channel_id="c-3",
        phone="+79990001124",
        provider_leg_id="direct-bridge-real",
        state=TelephonyLegState.ANSWERED,
        metadata={"internal_call_id": "call-bridge-real"},
    )

    class _Store:
        async def get(self, mango_leg_id: str):
            from app.integrations.telephony.mango_freeswitch_correlation import CorrelatedLegSnapshot

            return CorrelatedLegSnapshot(
                mango_leg_id=mango_leg_id,
                freeswitch_uuid="fs-real-bridge",
            )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(bridge_module, "get_mango_freeswitch_correlation_store", lambda: _Store())
        await bridge.open(channel)

    assert gateway.calls == [
        (
            "call-bridge-real",
            "fs-real-bridge",
            {"phone": "+79990001124", "mango_leg_id": "direct-bridge-real"},
        )
    ]


@pytest.mark.anyio
async def test_rtp_helpers_build_and_extract_payload():
    pcm = b"\x55" * 640
    packet = _build_rtp_packet(
        pcm=pcm,
        payload_type=96,
        seq=1,
        ts=160,
        ssrc=1234,
    )
    out = _extract_rtp_payload(packet)
    assert out == pcm


@pytest.mark.anyio
async def test_freeswitch_gateway_esl_rtp_ingest_inject_with_stubbed_esl():
    gw = FreeSwitchMediaGateway(
        FreeSwitchGatewayConfig(
            mode="esl_rtp",
            rtp_ip="127.0.0.1",
            rtp_port_start=25000,
            rtp_port_end=25100,
        )
    )
    gw._ensure_esl_connected = AsyncMock(return_value=None)  # type: ignore[method-assign]
    gw._run_attach_command = AsyncMock(return_value=None)  # type: ignore[method-assign]
    gw._run_hangup_command = AsyncMock(return_value=None)  # type: ignore[method-assign]

    handle = await gw.attach_session(call_id="call-rtp", provider_leg_id="leg-rtp")
    sid = handle.session_id
    runtime = gw._rtp[sid]

    snd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        snd.sendto(
            _build_rtp_packet(
                pcm=b"\x10" * 640,
                payload_type=96,
                seq=10,
                ts=160,
                ssrc=999,
            ),
            (runtime.local_ip, runtime.local_port),
        )
    finally:
        snd.close()

    events_iter = gw.events(sid)
    evt = await asyncio.wait_for(events_iter.__anext__(), timeout=1.0)
    assert evt.type == MediaEventType.AUDIO_IN
    assert evt.pcm == b"\x10" * 640

    await gw.send_audio(sid, b"\x20" * 640)
    assert gw.get_audio_out_bytes(sid) == 640

    await gw.detach_session(sid)


@pytest.mark.anyio
async def test_freeswitch_gateway_buffers_outbound_audio_until_first_inbound_rtp():
    gw = FreeSwitchMediaGateway(
        FreeSwitchGatewayConfig(
            mode="esl_rtp",
            rtp_ip="127.0.0.1",
            rtp_port_start=25110,
            rtp_port_end=25210,
        )
    )
    gw._ensure_esl_connected = AsyncMock(return_value=None)  # type: ignore[method-assign]
    gw._run_attach_command = AsyncMock(return_value=None)  # type: ignore[method-assign]
    gw._run_hangup_command = AsyncMock(return_value=None)  # type: ignore[method-assign]

    handle = await gw.attach_session(call_id="call-rtp-buffer", provider_leg_id="leg-rtp-buffer")
    sid = handle.session_id
    runtime = gw._rtp[sid]

    await gw.send_audio(sid, b"\x21" * 640)
    assert runtime.pending_outbound == [b"\x21" * 640]

    peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    peer.bind(("127.0.0.1", 0))
    peer.settimeout(1.0)
    try:
        peer.sendto(
            _build_rtp_packet(
                pcm=b"\x10" * 640,
                payload_type=96,
                seq=10,
                ts=160,
                ssrc=999,
            ),
            (runtime.local_ip, runtime.local_port),
        )
        packet, _ = await asyncio.wait_for(
            asyncio.to_thread(peer.recvfrom, 2048),
            timeout=1.0,
        )
    finally:
        peer.close()

    assert _extract_rtp_payload(packet) == b"\x21" * 640
    assert runtime.pending_outbound == []
    await gw.detach_session(sid)


@pytest.mark.anyio
async def test_freeswitch_gateway_attaches_immediately_when_direct_uuid_exists():
    gw = FreeSwitchMediaGateway(
        FreeSwitchGatewayConfig(
            mode="esl_rtp",
            rtp_ip="127.0.0.1",
            rtp_port_start=25110,
            rtp_port_end=25210,
        )
    )
    gw._ensure_esl_connected = AsyncMock(return_value=None)  # type: ignore[method-assign]
    gw._run_attach_command = AsyncMock(return_value=None)  # type: ignore[method-assign]
    gw._run_hangup_command = AsyncMock(return_value=None)  # type: ignore[method-assign]
    gw._direct_uuid_exists = AsyncMock(return_value=True)  # type: ignore[method-assign]

    handle = await gw.attach_session(
        call_id="call-direct-uuid",
        provider_leg_id="direct-leg-rtp",
    )
    sid = handle.session_id
    runtime = gw._rtp[sid]

    gw._run_attach_command.assert_awaited_once_with(  # type: ignore[attr-defined]
        "direct-leg-rtp",
        runtime.local_ip,
        runtime.local_port,
    )

    await gw.detach_session(sid)


@pytest.mark.anyio
async def test_freeswitch_correlation_only_event_ignores_provisional_hangup_when_real_uuid_known():
    gw = FreeSwitchMediaGateway(FreeSwitchGatewayConfig(mode="mock"))

    class _Store:
        def __init__(self) -> None:
            self.snap = CorrelatedLegSnapshot(
                mango_leg_id="direct-leg-1",
                freeswitch_uuid="fs-real-1",
            )
            self.upserts = []
            self.states = []

        async def get(self, mango_leg_id: str):
            assert mango_leg_id == "direct-leg-1"
            return self.snap

        async def upsert_mapping(self, **kwargs):
            self.upserts.append(kwargs)
            if kwargs.get("freeswitch_uuid"):
                self.snap.freeswitch_uuid = kwargs["freeswitch_uuid"]
            return self.snap

        async def set_freeswitch_state(self, **kwargs):
            self.states.append(kwargs)
            if kwargs.get("freeswitch_uuid"):
                self.snap.freeswitch_uuid = kwargs["freeswitch_uuid"]
            return self.snap

    store = _Store()
    gw._corr = store  # type: ignore[assignment]

    await gw._apply_correlation_only_event(
        "direct-leg-1",
        "channel_hangup",
        {"Event-Name": "CHANNEL_HANGUP", "Unique-ID": "direct-leg-1"},
        "direct-leg-1",
    )

    assert store.upserts == []
    assert store.states == []


@pytest.mark.anyio
async def test_freeswitch_correlation_only_event_ignores_foreign_uuid_after_real_uuid_known():
    gw = FreeSwitchMediaGateway(FreeSwitchGatewayConfig(mode="mock"))

    class _Store:
        def __init__(self) -> None:
            self.snap = CorrelatedLegSnapshot(
                mango_leg_id="direct-leg-2",
                freeswitch_uuid="fs-real-2",
            )
            self.upserts = []
            self.states = []

        async def get(self, mango_leg_id: str):
            assert mango_leg_id == "direct-leg-2"
            return self.snap

        async def upsert_mapping(self, **kwargs):
            self.upserts.append(kwargs)
            return self.snap

        async def set_freeswitch_state(self, **kwargs):
            self.states.append(kwargs)
            return self.snap

    store = _Store()
    gw._corr = store  # type: ignore[assignment]

    await gw._apply_correlation_only_event(
        "direct-leg-2",
        "channel_hangup",
        {"Event-Name": "CHANNEL_HANGUP", "Unique-ID": "fs-child-9"},
        "fs-child-9",
    )

    assert store.upserts == []
    assert store.states == []


@pytest.mark.anyio
async def test_freeswitch_gateway_primes_remote_endpoint_from_esl_event_and_flushes_audio():
    gw = FreeSwitchMediaGateway(
        FreeSwitchGatewayConfig(
            mode="esl_rtp",
            rtp_ip="127.0.0.1",
            rtp_port_start=25220,
            rtp_port_end=25320,
        )
    )
    gw._ensure_esl_connected = AsyncMock(return_value=None)  # type: ignore[method-assign]
    gw._run_attach_command = AsyncMock(return_value=None)  # type: ignore[method-assign]
    gw._run_hangup_command = AsyncMock(return_value=None)  # type: ignore[method-assign]

    handle = await gw.attach_session(call_id="call-rtp-prime", provider_leg_id="leg-rtp-prime")
    sid = handle.session_id
    runtime = gw._rtp[sid]

    peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    peer.bind(("127.0.0.1", 0))
    peer.settimeout(1.0)
    host, port = peer.getsockname()
    try:
        await gw.send_audio(sid, b"\x22" * 640)
        await gw._process_plain_event(
            {
                "Event-Name": "CHANNEL_ANSWER",
                "Unique-ID": "leg-rtp-prime",
                "variable_remote_media_ip": host,
                "variable_remote_media_port": str(port),
            }
        )
        packet, _ = peer.recvfrom(2048)
    finally:
        peer.close()

    assert _extract_rtp_payload(packet) == b"\x22" * 640
    assert runtime.remote_addr == (host, port)
    assert runtime.pending_outbound == []
    await gw.detach_session(sid)


@pytest.mark.anyio
async def test_freeswitch_gateway_event_normalization_updates_lifecycle_and_correlation():
    gw = FreeSwitchMediaGateway(FreeSwitchGatewayConfig(mode="mock"))
    handle = await gw.attach_session(call_id="call-corr", provider_leg_id="leg-corr")
    sid = handle.session_id

    await gw._process_plain_event({"Event-Name": "CHANNEL_CREATE", "Unique-ID": "leg-corr"})
    await gw._process_plain_event({"Event-Name": "CHANNEL_ANSWER", "Unique-ID": "leg-corr"})
    await gw._process_plain_event({"Event-Name": "PLAYBACK_START", "Unique-ID": "leg-corr"})
    await gw._process_plain_event({"Event-Name": "PLAYBACK_STOP", "Unique-ID": "leg-corr"})
    await gw._process_plain_event({"Event-Name": "CHANNEL_BRIDGE", "Unique-ID": "leg-corr"})

    corr = gw.get_session_correlation(sid)
    assert corr is not None
    assert corr["call_id"] == "call-corr"
    assert corr["mango_leg_id"] == "leg-corr"
    assert corr["freeswitch_uuid"] == "leg-corr"

    state = gw.get_session_lifecycle(sid)
    assert state is not None
    assert state["created"] is True
    assert state["answered"] is True
    assert state["bridged"] is True
    assert state["playback_active"] is False
    corr_store_snap = await gw._corr.get("leg-corr")  # type: ignore[attr-defined]
    assert corr_store_snap is not None
    assert corr_store_snap.effective_state == TelephonyLegState.BRIDGED


@pytest.mark.anyio
async def test_freeswitch_gateway_correlation_only_events_track_outbound_leg_before_session_attach():
    gw = FreeSwitchMediaGateway(FreeSwitchGatewayConfig(mode="mock"))

    await gw._process_plain_event(
        {
            "Event-Name": "CHANNEL_CREATE",
            "Unique-ID": "fs-real-1",
            "variable_origination_uuid": "direct-pre-answer",
        }
    )
    await gw._process_plain_event(
        {
            "Event-Name": "CHANNEL_ANSWER",
            "Unique-ID": "fs-real-1",
            "variable_origination_uuid": "direct-pre-answer",
        }
    )

    corr_store_snap = await gw._corr.get("direct-pre-answer")  # type: ignore[attr-defined]
    assert corr_store_snap is not None
    assert corr_store_snap.freeswitch_uuid == "fs-real-1"
    assert corr_store_snap.effective_state == TelephonyLegState.ANSWERED


@pytest.mark.anyio
async def test_freeswitch_gateway_answer_event_can_resolve_real_uuid_after_create():
    gw = FreeSwitchMediaGateway(FreeSwitchGatewayConfig(mode="mock"))

    await gw._process_plain_event(
        {
            "Event-Name": "CHANNEL_CREATE",
            "Unique-ID": "fs-real-2",
            "variable_origination_uuid": "direct-post-create",
        }
    )
    await gw._process_plain_event(
        {
            "Event-Name": "CHANNEL_ANSWER",
            "Unique-ID": "fs-real-2",
        }
    )

    corr_store_snap = await gw._corr.get("direct-post-create")  # type: ignore[attr-defined]
    assert corr_store_snap is not None
    assert corr_store_snap.freeswitch_uuid == "fs-real-2"
    assert corr_store_snap.effective_state == TelephonyLegState.ANSWERED


@pytest.mark.anyio
async def test_freeswitch_gateway_promotes_session_uuid_and_retargets_attach_when_real_uuid_arrives():
    gw = FreeSwitchMediaGateway(
        FreeSwitchGatewayConfig(
            mode="esl_rtp",
            rtp_ip="127.0.0.1",
            rtp_port_start=25330,
            rtp_port_end=25430,
        )
    )
    gw._ensure_esl_connected = AsyncMock(return_value=None)  # type: ignore[method-assign]
    gw._run_attach_command = AsyncMock(return_value=None)  # type: ignore[method-assign]
    gw._run_hangup_command = AsyncMock(return_value=None)  # type: ignore[method-assign]

    handle = await gw.attach_session(
        call_id="call-promote",
        provider_leg_id="fs-real-placeholder",
        metadata={"mango_leg_id": "direct-promote"},
    )
    sid = handle.session_id
    gw._correlation[sid].freeswitch_uuid = "direct-promote"  # type: ignore[attr-defined]
    gw._uuid_to_session.pop("fs-real-placeholder", None)  # type: ignore[attr-defined]
    gw._uuid_to_session["direct-promote"] = sid  # type: ignore[attr-defined]

    await gw._process_plain_event(
        {
            "Event-Name": "CHANNEL_CREATE",
            "Unique-ID": "fs-real-3",
            "variable_origination_uuid": "direct-promote",
        }
    )
    await gw._process_plain_event(
        {
            "Event-Name": "CHANNEL_ANSWER",
            "Unique-ID": "fs-real-3",
        }
    )

    corr = gw.get_session_correlation(sid)
    assert corr is not None
    assert corr["freeswitch_uuid"] == "fs-real-3"
    gw._run_attach_command.assert_any_call("fs-real-3", gw._rtp[sid].local_ip, gw._rtp[sid].local_port)  # type: ignore[index]
    await gw.detach_session(sid)


@pytest.mark.anyio
async def test_freeswitch_gateway_defers_attach_for_provisional_direct_uuid_until_promotion():
    gw = FreeSwitchMediaGateway(
        FreeSwitchGatewayConfig(
            mode="esl_rtp",
            rtp_ip="127.0.0.1",
            rtp_port_start=25440,
            rtp_port_end=25540,
        )
    )
    gw._ensure_esl_connected = AsyncMock(return_value=None)  # type: ignore[method-assign]
    gw._run_attach_command = AsyncMock(return_value=None)  # type: ignore[method-assign]
    gw._run_hangup_command = AsyncMock(return_value=None)  # type: ignore[method-assign]

    handle = await gw.attach_session(
        call_id="call-direct-provisional",
        provider_leg_id="direct-provisional-1",
        metadata={"mango_leg_id": "direct-provisional-1"},
    )
    sid = handle.session_id

    gw._run_attach_command.assert_not_awaited()

    await gw._process_plain_event(
        {
            "Event-Name": "CHANNEL_CREATE",
            "Unique-ID": "fs-real-promoted-1",
            "variable_origination_uuid": "direct-provisional-1",
        }
    )

    gw._run_attach_command.assert_awaited_with(
        "fs-real-promoted-1",
        gw._rtp[sid].local_ip,
        gw._rtp[sid].local_port,
    )  # type: ignore[index]
    await gw.detach_session(sid)


@pytest.mark.anyio
async def test_freeswitch_gateway_hangup_event_propagates_to_media_stream():
    gw = FreeSwitchMediaGateway(FreeSwitchGatewayConfig(mode="mock"))
    handle = await gw.attach_session(call_id="call-hang", provider_leg_id="leg-hang")
    sid = handle.session_id

    await gw._process_plain_event(
        {
            "Event-Name": "CHANNEL_HANGUP_COMPLETE",
            "Unique-ID": "leg-hang",
            "Hangup-Cause": "NORMAL_CLEARING",
        }
    )

    events_iter = gw.events(sid)
    evt = await asyncio.wait_for(events_iter.__anext__(), timeout=1.0)
    assert evt.type == MediaEventType.HANGUP
    assert evt.reason == "NORMAL_CLEARING"


@pytest.mark.anyio
async def test_freeswitch_gateway_esl_connect_retries_then_succeeds():
    gw = FreeSwitchMediaGateway(
        FreeSwitchGatewayConfig(
            mode="esl_rtp",
            esl_reconnect_enabled=True,
            esl_reconnect_initial_delay_seconds=0.001,
            esl_reconnect_max_delay_seconds=0.002,
            esl_reconnect_max_attempts=3,
        )
    )

    class _FakeEsl:
        def __init__(self, ok: bool):
            self._ok = ok
            self.connected = False

        async def connect(self):
            if not self._ok:
                raise RuntimeError("connect failed")
            self.connected = True

        async def subscribe_events(self, events: str):
            return None

        async def read_frame(self):
            await asyncio.sleep(3600)

        async def close(self):
            self.connected = False

    clients = [_FakeEsl(False), _FakeEsl(True)]
    gw._build_esl_client = lambda: clients.pop(0)  # type: ignore[method-assign]
    await gw._ensure_esl_connected()
    assert gw._esl is not None


@pytest.mark.anyio
async def test_freeswitch_gateway_sync_execute_uses_ephemeral_connection_when_reader_active():
    gw = FreeSwitchMediaGateway(FreeSwitchGatewayConfig(mode="esl_rtp"))

    class _PersistentEsl:
        connected = True

        async def send_api(self, command: str, background: bool = True):
            raise AssertionError("persistent ESL connection should not be used for sync probe")

    class _ProbeEsl:
        def __init__(self):
            self.connected = False
            self.commands: list[tuple[str, bool]] = []

        async def connect(self):
            self.connected = True

        async def send_api(self, command: str, background: bool = True):
            self.commands.append((command, background))
            return "false"

        async def close(self):
            self.connected = False

    probe = _ProbeEsl()
    gw._esl = _PersistentEsl()  # type: ignore[assignment]
    gw._build_esl_client = lambda: probe  # type: ignore[method-assign]
    gw._esl_reader_task = asyncio.create_task(asyncio.sleep(3600))
    gw._ensure_esl_connected = AsyncMock(return_value=None)  # type: ignore[method-assign]

    try:
        reply = await gw.execute_command("uuid_exists test-leg", background=False)
    finally:
        gw._esl_reader_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await gw._esl_reader_task

    assert reply == "false"
    assert probe.commands == [("uuid_exists test-leg", False)]


def test_freeswitch_pcmu_codec_roundtrip_has_signal():
    pcm = (b"\x00\x00" + b"\xff\x7f" + b"\x01\x80") * 40
    ulaw = _encode_outbound_audio(pcm, "pcmu", 16000)
    restored = _decode_inbound_audio(ulaw, inbound_codec="pcmu", payload_type=0, sample_rate_hz=16000)
    assert len(restored) == len(pcm)
    assert any(b != 0 for b in restored)


def test_freeswitch_pcmu_8khz_roundtrip_resamples_back_to_16khz():
    pcm = (b"\x00\x00" + b"\x20\x20" + b"\xe0\xe0" + b"\xff\x7f") * 80
    ulaw = _encode_outbound_audio(pcm, "pcmu", 8000)
    restored = _decode_inbound_audio(
        ulaw,
        inbound_codec="pcmu",
        payload_type=0,
        sample_rate_hz=8000,
    )
    assert len(restored) >= len(pcm) - 4
    assert any(b != 0 for b in restored)


@pytest.mark.anyio
async def test_freeswitch_gateway_stats_and_timeout_hangup():
    gw = FreeSwitchMediaGateway(
        FreeSwitchGatewayConfig(
            mode="esl_rtp",
            rtp_ip="127.0.0.1",
            rtp_port_start=25200,
            rtp_port_end=25300,
            rtp_inbound_timeout_seconds=1,
        )
    )
    gw._ensure_esl_connected = AsyncMock(return_value=None)  # type: ignore[method-assign]
    gw._run_attach_command = AsyncMock(return_value=None)  # type: ignore[method-assign]
    gw._run_hangup_command = AsyncMock(return_value=None)  # type: ignore[method-assign]

    handle = await gw.attach_session(call_id="call-stats", provider_leg_id="leg-stats")
    sid = handle.session_id
    runtime = gw._rtp[sid]

    snd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        snd.sendto(
            _build_rtp_packet(
                pcm=b"\x10" * 320,
                payload_type=96,
                seq=1,
                ts=80,
                ssrc=111,
            ),
            (runtime.local_ip, runtime.local_port),
        )
    finally:
        snd.close()

    await asyncio.sleep(0.1)
    stats = gw.get_media_stats(sid)
    assert stats is not None
    assert stats["frames_in"] >= 1
    assert stats["bytes_in"] >= 320

    # Wait for watchdog timeout to emit hangup event.
    events_iter = gw.events(sid)
    got_hangup = False
    for _ in range(3):
        evt = await asyncio.wait_for(events_iter.__anext__(), timeout=2.0)
        if evt.type == MediaEventType.HANGUP and evt.reason == "rtp_timeout":
            got_hangup = True
            break
    assert got_hangup is True

    await gw.detach_session(sid)


@pytest.mark.anyio
async def test_freeswitch_gateway_remote_timeout_hangup_without_inbound_rtp():
    gw = FreeSwitchMediaGateway(
        FreeSwitchGatewayConfig(
            mode="esl_rtp",
            rtp_ip="127.0.0.1",
            rtp_port_start=25340,
            rtp_port_end=25440,
            rtp_inbound_timeout_seconds=1,
        )
    )
    gw._ensure_esl_connected = AsyncMock(return_value=None)  # type: ignore[method-assign]
    gw._run_attach_command = AsyncMock(return_value=None)  # type: ignore[method-assign]
    gw._run_hangup_command = AsyncMock(return_value=None)  # type: ignore[method-assign]

    handle = await gw.attach_session(call_id="call-remote-timeout", provider_leg_id="leg-remote-timeout")
    sid = handle.session_id

    events_iter = gw.events(sid)
    got_hangup = False
    for _ in range(2):
        evt = await asyncio.wait_for(events_iter.__anext__(), timeout=2.5)
        if evt.type == MediaEventType.HANGUP and evt.reason == "rtp_remote_timeout":
            got_hangup = True
            break
    assert got_hangup is True

    await gw.detach_session(sid)

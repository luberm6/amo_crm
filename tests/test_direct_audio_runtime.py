from __future__ import annotations

import asyncio
import uuid
from unittest.mock import patch

import pytest

from app.core.config import settings
from app.core.audio_utils import analyze_pcm16_audibility, pcm16_duration_ms_for_bytes
from app.core.exceptions import EngineError
from app.integrations.direct.session_manager import (
    DirectSession,
    DirectSessionCapabilities,
    DirectSessionManager,
    _resample_pcm16,
)
from app.integrations.direct.voice_strategy import make_session_voice_state_for_strategy
from app.integrations.telephony.audio_bridge import AbstractAudioBridge
from app.integrations.telephony.base import (
    AbstractTelephonyAdapter,
    TelephonyChannel,
    TelephonyLegState,
    TelephonyOriginateResult,
)
from app.integrations.telephony.capabilities import ProviderCapabilities
from app.integrations.voice.base import AbstractVoiceProvider
from app.models.call import Call, CallStatus
from tests.conftest import MockGeminiLiveClient


class _AudioBridge(AbstractAudioBridge):
    def __init__(self) -> None:
        self._is_open = False
        self._in_q: asyncio.Queue[bytes] = asyncio.Queue()
        self.played: list[bytes] = []

    async def open(self, channel: TelephonyChannel) -> None:
        self._is_open = True

    async def close(self) -> None:
        self._is_open = False

    @property
    def is_open(self) -> bool:
        return self._is_open

    async def audio_in(self):
        while self._is_open:
            chunk = await self._in_q.get()
            if chunk == b"__stop__":
                return
            yield chunk

    async def audio_out(self, pcm: bytes) -> None:
        self.played.append(pcm)

    async def inject_in(self, pcm: bytes) -> None:
        await self._in_q.put(pcm)

    async def stop(self) -> None:
        await self._in_q.put(b"__stop__")


class _FailingAudioBridge(_AudioBridge):
    async def audio_in(self):
        raise RuntimeError("bridge_reader_failure")
        yield b""


class _VoiceProvider(AbstractVoiceProvider):
    async def synthesize(self, text: str, voice_id=None) -> bytes:
        return b"\x11" * 640

    async def synthesize_streaming(self, text: str, voice_id=None):
        yield b"\x22" * 640
        yield b"\x33" * 640


class _FailingVoiceProvider(AbstractVoiceProvider):
    async def synthesize(self, text: str, voice_id=None) -> bytes:
        raise RuntimeError("tts synth failed")

    async def synthesize_streaming(self, text: str, voice_id=None):
        raise RuntimeError("tts stream failed")
        yield b""


class _LeadingSilenceVoiceProvider(AbstractVoiceProvider):
    async def synthesize(self, text: str, voice_id=None) -> bytes:
        return b""

    async def synthesize_streaming(self, text: str, voice_id=None):
        yield b"\x00" * 640
        yield (int(300).to_bytes(2, "little", signed=True)) * 320
        yield (b"\x00\x00" * 160) + (int(9000).to_bytes(2, "little", signed=True) * 160)
        yield b"\x00" * 640


async def _get_call_status(session_factory, call_id):
    async with session_factory() as session:
        call = await session.get(Call, call_id)
        return call.status if call is not None else None


async def _create_call_record(session_factory, call_id, phone="+79991234567"):
    async with session_factory() as session:
        call = Call(id=call_id, phone=phone, status=CallStatus.IN_PROGRESS)
        session.add(call)
        await session.commit()


def test_session_manager_audio_out_alignment_preserves_pcm16_stream() -> None:
    sm = DirectSessionManager()
    loop = asyncio.new_event_loop()
    previous_loop = None
    try:
        try:
            previous_loop = asyncio.get_event_loop()
        except RuntimeError:
            previous_loop = None
        asyncio.set_event_loop(loop)
        direct_session = DirectSession(
            session_id="test-direct",
            call_id=uuid.uuid4(),
            phone="+79991230000",
        )
    finally:
        asyncio.set_event_loop(previous_loop)
        loop.close()

    first = sm._enqueue_audio_out(direct_session, b"\x01", "tts_primary")
    second = sm._enqueue_audio_out(direct_session, b"\x02\x03", "tts_primary")
    third = sm._enqueue_audio_out(direct_session, b"\x04\x05\x06", "tts_primary")
    tail = sm._flush_audio_out_alignment(direct_session, "tts_primary")

    assert first == b""
    assert second == b"\x01\x02"
    assert third == b"\x03\x04\x05\x06"
    assert tail == b""


@pytest.mark.anyio
async def test_tts_voiced_first_gate_trims_leading_silence_but_preserves_mid_utterance_silence() -> None:
    sm = DirectSessionManager()
    bridge = _AudioBridge()
    session = DirectSession(
        session_id="leading-silence-direct",
        call_id=uuid.uuid4(),
        phone="+79990001003",
        audio_bridge=bridge,
        voice_provider=_LeadingSilenceVoiceProvider(),
        capabilities=DirectSessionCapabilities(
            mode="audio_out_only",
            text_only=False,
            audio_out=True,
            real_audio_out=True,
        ),
        voice_state=make_session_voice_state_for_strategy("tts_primary"),
    )

    await sm._synthesize_to_audio_queue(session, session.voice_provider, "test")
    await sm._drain_audio_out_queue(session)

    assert bridge.played
    first_chunk = bridge.played[0]
    first_chunk_analysis = analyze_pcm16_audibility(first_chunk)
    assert first_chunk_analysis.first_voiced_offset_ms is not None
    assert first_chunk_analysis.first_voiced_offset_ms <= 2.5
    assert session.metrics.tts_provider_leading_silence_ms_last is not None
    assert session.metrics.tts_provider_leading_silence_ms_last >= 40
    assert session.metrics.tts_backend_leading_silence_ms_last is not None
    assert session.metrics.tts_backend_leading_silence_ms_last >= 8
    assert session.metrics.tts_leading_silence_trimmed_ms_last is not None
    assert session.metrics.tts_leading_silence_trimmed_ms_last >= 40
    assert session.metrics.tts_first_non_silent_chunk_sent_ms_last is not None
    assert session.metrics.tts_first_non_silent_chunk_played_ms_last is not None
    assert any(
        pcm16_duration_ms_for_bytes(len(chunk)) >= 20 and chunk == b"\x00" * len(chunk)
        for chunk in bridge.played[1:]
    )


@pytest.mark.anyio
async def test_initial_greeting_for_tts_primary_uses_direct_tts_without_gemini_instruction(
    test_session_factory,
) -> None:
    old_el_enabled = settings.elevenlabs_enabled
    old_el_key = settings.elevenlabs_api_key
    old_el_voice = settings.elevenlabs_voice_id
    old_greeting_enabled = settings.direct_initial_greeting_enabled
    old_greeting_text = settings.direct_initial_greeting_text
    old_voice_strategy = settings.direct_voice_strategy
    try:
        settings.direct_voice_strategy = "tts_primary"
        settings.elevenlabs_enabled = True
        settings.elevenlabs_api_key = "k"
        settings.elevenlabs_voice_id = "v"
        settings.direct_initial_greeting_enabled = True
        settings.direct_initial_greeting_text = "Здравствуйте!"

        sm = DirectSessionManager()
        bridge = _AudioBridge()
        telephony = BridgeTelephonyAdapter(bridge=bridge)
        voice = _VoiceProvider()

        with patch("app.integrations.direct.session_manager.GeminiLiveClient", new=MockGeminiLiveClient):
            sid = await sm.create_session(
                call_id=uuid.uuid4(),
                phone="+79991230001",
                telephony=telephony,
                voice=voice,
                session_factory=test_session_factory,
                system_prompt="test",
            )

        session = sm.get_session(sid)
        assert session is not None
        await asyncio.sleep(0.05)
        assert session.instruction_queue.empty()
        await sm._drain_audio_out_queue(session)
        assert bridge.played
        assert session.metrics.awaiting_model_response is False
        await sm.terminate_session(sid)
    finally:
        settings.direct_voice_strategy = old_voice_strategy
        settings.elevenlabs_enabled = old_el_enabled
        settings.elevenlabs_api_key = old_el_key
        settings.elevenlabs_voice_id = old_el_voice
        settings.direct_initial_greeting_enabled = old_greeting_enabled
        settings.direct_initial_greeting_text = old_greeting_text


class BridgeTelephonyAdapter(AbstractTelephonyAdapter):
    def __init__(self, bridge: AbstractAudioBridge, fail_attach: bool = False) -> None:
        self._bridge = bridge
        self._fail_attach = fail_attach

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_name="test",
            supports_outbound_call=True,
            supports_audio_stream=True,
            supports_audio_bridge=True,
        )

    async def connect(self, phone: str, caller_id=None, metadata=None) -> TelephonyChannel:
        return TelephonyChannel(
            channel_id=f"ch-{phone}",
            phone=phone,
            provider_leg_id=f"leg-{phone}",
            state=TelephonyLegState.ANSWERED,
        )

    async def disconnect(self, phone: str) -> None:
        return None

    async def audio_stream(self, channel: TelephonyChannel):
        return
        yield

    async def send_audio(self, channel: TelephonyChannel, pcm_bytes: bytes) -> None:
        return None

    async def attach_audio_bridge(self, channel: TelephonyChannel):
        if self._fail_attach:
            raise RuntimeError("no bridge")
        await self._bridge.open(channel)
        return self._bridge

    async def detach_audio_bridge(self, bridge: AbstractAudioBridge) -> None:
        await bridge.close()

    async def originate_call(self, phone: str, caller_id=None, metadata=None):
        return TelephonyOriginateResult(leg_id=f"leg-{phone}")

    async def bridge_legs(self, customer_leg_id: str, manager_leg_id: str) -> None:
        return None

    async def play_whisper(self, leg_id: str, message: str) -> None:
        return None

    async def terminate_leg(self, leg_id: str) -> None:
        return None

    async def get_leg_state(self, leg_id: str) -> TelephonyLegState:
        return TelephonyLegState.ANSWERED


@pytest.mark.anyio
async def test_session_capabilities_text_only_when_bridge_unavailable(test_session_factory):
    old_voice_strategy = settings.direct_voice_strategy
    old_gem_audio = settings.gemini_audio_output_enabled
    sm = DirectSessionManager()
    telephony = BridgeTelephonyAdapter(bridge=_AudioBridge(), fail_attach=True)
    voice = _VoiceProvider()
    try:
        settings.direct_voice_strategy = "gemini_primary"
        settings.gemini_audio_output_enabled = True
        with patch("app.integrations.direct.session_manager.GeminiLiveClient", new=MockGeminiLiveClient):
            with pytest.raises(EngineError):
                await sm.create_session(
                    call_id=uuid.uuid4(),
                    phone="+79991234567",
                    telephony=telephony,
                    voice=voice,
                    session_factory=test_session_factory,
                    system_prompt="test",
                )
    finally:
        settings.direct_voice_strategy = old_voice_strategy
        settings.gemini_audio_output_enabled = old_gem_audio


@pytest.mark.anyio
async def test_disabled_voice_strategy_blocks_session_start(test_session_factory):
    old_voice_strategy = settings.direct_voice_strategy
    sm = DirectSessionManager()
    telephony = BridgeTelephonyAdapter(bridge=_AudioBridge())
    voice = _VoiceProvider()
    try:
        settings.direct_voice_strategy = "disabled"
        with patch("app.integrations.direct.session_manager.GeminiLiveClient", new=MockGeminiLiveClient):
            with pytest.raises(EngineError):
                await sm.create_session(
                    call_id=uuid.uuid4(),
                    phone="+79991234016",
                    telephony=telephony,
                    voice=voice,
                    session_factory=test_session_factory,
                    system_prompt="test",
                )
    finally:
        settings.direct_voice_strategy = old_voice_strategy


@pytest.mark.anyio
async def test_session_capabilities_full_duplex_with_audio_enabled(test_session_factory):
    old_gem_audio = settings.gemini_audio_output_enabled
    old_gem_audio_in = settings.gemini_audio_input_enabled
    old_voice_strategy = settings.direct_voice_strategy
    try:
        settings.direct_voice_strategy = "gemini_primary"
        settings.gemini_audio_output_enabled = True
        settings.gemini_audio_input_enabled = True
        sm = DirectSessionManager()
        telephony = BridgeTelephonyAdapter(bridge=_AudioBridge())
        voice = _VoiceProvider()
        with patch("app.integrations.direct.session_manager.GeminiLiveClient", new=MockGeminiLiveClient):
            sid = await sm.create_session(
                call_id=uuid.uuid4(),
                phone="+79991234568",
                telephony=telephony,
                voice=voice,
                session_factory=test_session_factory,
                system_prompt="test",
            )
        caps = sm.get_session_capabilities(sid)
        assert caps is not None
        assert caps.full_duplex is True
        assert caps.audio_in is True
        assert caps.audio_out is True
        await sm.terminate_session(sid)
    finally:
        settings.direct_voice_strategy = old_voice_strategy
        settings.gemini_audio_output_enabled = old_gem_audio
        settings.gemini_audio_input_enabled = old_gem_audio_in


@pytest.mark.anyio
async def test_audio_in_drops_idle_silence_before_model_turn() -> None:
    sm = DirectSessionManager()
    session = DirectSession(
        session_id="silence-gate-direct",
        call_id=uuid.uuid4(),
        phone="+79990001004",
        capabilities=DirectSessionCapabilities(audio_in=True, real_audio_in=True),
    )
    session.gemini_client = MockGeminiLiveClient(
        on_text=lambda *_: None,
        on_audio=lambda *_: None,
        on_close=lambda: None,
    )

    await session.audio_in_queue.put((b"\x00" * 640, asyncio.get_running_loop().time()))
    await sm._drain_audio_in_queue(session)

    assert session.gemini_client.sent_audio_chunks == []
    assert session.metrics.inbound_silence_chunks_dropped == 1
    assert session.metrics.awaiting_model_response is False


@pytest.mark.anyio
async def test_audio_in_sends_voiced_chunk_and_trailing_silence() -> None:
    sm = DirectSessionManager()
    session = DirectSession(
        session_id="voice-gate-direct",
        call_id=uuid.uuid4(),
        phone="+79990001005",
        capabilities=DirectSessionCapabilities(audio_in=True, real_audio_in=True),
    )
    session.gemini_client = MockGeminiLiveClient(
        on_text=lambda *_: None,
        on_audio=lambda *_: None,
        on_close=lambda: None,
    )
    voiced = (int(3000).to_bytes(2, "little", signed=True)) * 320
    silence = b"\x00" * 640

    await session.audio_in_queue.put((voiced, asyncio.get_running_loop().time()))
    await session.audio_in_queue.put((silence, asyncio.get_running_loop().time()))
    await sm._drain_audio_in_queue(session)

    assert session.gemini_client.sent_audio_chunks == [voiced, silence]
    assert session.metrics.inbound_silence_chunks_dropped == 0
    assert session.metrics.awaiting_model_response is True


@pytest.mark.anyio
async def test_session_capabilities_audio_in_only_mode(test_session_factory):
    old_gem_audio = settings.gemini_audio_output_enabled
    old_gem_audio_in = settings.gemini_audio_input_enabled
    old_el_enabled = settings.elevenlabs_enabled
    old_el_key = settings.elevenlabs_api_key
    old_el_voice = settings.elevenlabs_voice_id
    old_voice_strategy = settings.direct_voice_strategy
    try:
        settings.direct_voice_strategy = "gemini_primary"
        settings.gemini_audio_input_enabled = True
        settings.gemini_audio_output_enabled = False
        settings.elevenlabs_enabled = False
        settings.elevenlabs_api_key = ""
        settings.elevenlabs_voice_id = ""
        sm = DirectSessionManager()
        telephony = BridgeTelephonyAdapter(bridge=_AudioBridge())
        voice = _VoiceProvider()
        with patch("app.integrations.direct.session_manager.GeminiLiveClient", new=MockGeminiLiveClient):
            with pytest.raises(EngineError):
                await sm.create_session(
                    call_id=uuid.uuid4(),
                    phone="+79990001001",
                    telephony=telephony,
                    voice=voice,
                    session_factory=test_session_factory,
                    system_prompt="test",
                )
    finally:
        settings.direct_voice_strategy = old_voice_strategy
        settings.gemini_audio_output_enabled = old_gem_audio
        settings.gemini_audio_input_enabled = old_gem_audio_in
        settings.elevenlabs_enabled = old_el_enabled
        settings.elevenlabs_api_key = old_el_key
        settings.elevenlabs_voice_id = old_el_voice


@pytest.mark.anyio
async def test_session_capabilities_audio_out_only_mode(test_session_factory):
    old_gem_audio = settings.gemini_audio_output_enabled
    old_gem_audio_in = settings.gemini_audio_input_enabled
    old_el_enabled = settings.elevenlabs_enabled
    old_el_key = settings.elevenlabs_api_key
    old_el_voice = settings.elevenlabs_voice_id
    old_voice_strategy = settings.direct_voice_strategy
    try:
        settings.direct_voice_strategy = "tts_primary"
        settings.gemini_audio_input_enabled = False
        settings.gemini_audio_output_enabled = False
        settings.elevenlabs_enabled = True
        settings.elevenlabs_api_key = "k"
        settings.elevenlabs_voice_id = "v"
        sm = DirectSessionManager()
        telephony = BridgeTelephonyAdapter(bridge=_AudioBridge())
        voice = _VoiceProvider()
        with patch("app.integrations.direct.session_manager.GeminiLiveClient", new=MockGeminiLiveClient):
            sid = await sm.create_session(
                call_id=uuid.uuid4(),
                phone="+79990001002",
                telephony=telephony,
                voice=voice,
                session_factory=test_session_factory,
                system_prompt="test",
            )
        caps = sm.get_session_capabilities(sid)
        assert caps is not None
        assert caps.mode == "audio_out_only"
        assert caps.audio_in is False
        assert caps.audio_out is True
        await sm.terminate_session(sid)
    finally:
        settings.direct_voice_strategy = old_voice_strategy
        settings.gemini_audio_output_enabled = old_gem_audio
        settings.gemini_audio_input_enabled = old_gem_audio_in
        settings.elevenlabs_enabled = old_el_enabled
        settings.elevenlabs_api_key = old_el_key
        settings.elevenlabs_voice_id = old_el_voice


@pytest.mark.anyio
async def test_fallback_voice_path_enqueues_and_plays_tts_audio(test_session_factory):
    old_gem_audio = settings.gemini_audio_output_enabled
    old_el_enabled = settings.elevenlabs_enabled
    old_el_key = settings.elevenlabs_api_key
    old_el_voice = settings.elevenlabs_voice_id
    old_voice_strategy = settings.direct_voice_strategy
    try:
        settings.direct_voice_strategy = "tts_primary"
        settings.gemini_audio_output_enabled = False
        settings.elevenlabs_enabled = True
        settings.elevenlabs_api_key = "k"
        settings.elevenlabs_voice_id = "v"

        sm = DirectSessionManager()
        bridge = _AudioBridge()
        telephony = BridgeTelephonyAdapter(bridge=bridge)
        voice = _VoiceProvider()

        with patch("app.integrations.direct.session_manager.GeminiLiveClient", new=MockGeminiLiveClient):
            sid = await sm.create_session(
                call_id=uuid.uuid4(),
                phone="+79991234569",
                telephony=telephony,
                voice=voice,
                session_factory=test_session_factory,
                system_prompt="test",
            )

        session = sm.get_session(sid)
        assert session is not None
        assert session.gemini_client is not None
        session.gemini_client.simulate_text("assistant", "Привет")
        await asyncio.sleep(0.05)
        assert bridge.played, "TTS fallback audio must be delivered to bridge"
        await sm.terminate_session(sid)
    finally:
        settings.direct_voice_strategy = old_voice_strategy
        settings.gemini_audio_output_enabled = old_gem_audio
        settings.elevenlabs_enabled = old_el_enabled
        settings.elevenlabs_api_key = old_el_key
        settings.elevenlabs_voice_id = old_el_voice


@pytest.mark.anyio
async def test_initial_greeting_plays_immediately_over_tts(test_session_factory):
    old_gem_audio = settings.gemini_audio_output_enabled
    old_el_enabled = settings.elevenlabs_enabled
    old_el_key = settings.elevenlabs_api_key
    old_el_voice = settings.elevenlabs_voice_id
    old_greeting_enabled = settings.direct_initial_greeting_enabled
    old_greeting_text = settings.direct_initial_greeting_text
    old_voice_strategy = settings.direct_voice_strategy
    try:
        settings.direct_voice_strategy = "tts_primary"
        settings.gemini_audio_output_enabled = False
        settings.elevenlabs_enabled = True
        settings.elevenlabs_api_key = "k"
        settings.elevenlabs_voice_id = "v"
        settings.direct_initial_greeting_enabled = True
        settings.direct_initial_greeting_text = "Здравствуйте"

        sm = DirectSessionManager()
        bridge = _AudioBridge()
        telephony = BridgeTelephonyAdapter(bridge=bridge)
        voice = _VoiceProvider()

        with patch("app.integrations.direct.session_manager.GeminiLiveClient", new=MockGeminiLiveClient):
            sid = await sm.create_session(
                call_id=uuid.uuid4(),
                phone="+79991234010",
                telephony=telephony,
                voice=voice,
                session_factory=test_session_factory,
                system_prompt="test",
            )

        try:
            session = sm.get_session(sid)
            assert session is not None
            assert session.gemini_client is not None
            await asyncio.sleep(0.05)
            assert session.gemini_client.injected_instructions == []
            await sm._drain_audio_out_queue(session)
            assert bridge.played[:2] == [b"\x22" * 640, b"\x33" * 640]
            assert session.metrics.awaiting_model_response is False
        finally:
            await sm.terminate_session(sid)
    finally:
        settings.direct_voice_strategy = old_voice_strategy
        settings.gemini_audio_output_enabled = old_gem_audio
        settings.elevenlabs_enabled = old_el_enabled
        settings.elevenlabs_api_key = old_el_key
        settings.elevenlabs_voice_id = old_el_voice
        settings.direct_initial_greeting_enabled = old_greeting_enabled
        settings.direct_initial_greeting_text = old_greeting_text


@pytest.mark.anyio
async def test_initial_greeting_falls_back_to_gemini_instruction_when_tts_unavailable(
    test_session_factory,
):
    old_gem_audio = settings.gemini_audio_output_enabled
    old_el_enabled = settings.elevenlabs_enabled
    old_el_key = settings.elevenlabs_api_key
    old_el_voice = settings.elevenlabs_voice_id
    old_greeting_enabled = settings.direct_initial_greeting_enabled
    old_greeting_text = settings.direct_initial_greeting_text
    old_voice_strategy = settings.direct_voice_strategy
    try:
        settings.direct_voice_strategy = "gemini_primary"
        settings.gemini_audio_output_enabled = True
        settings.elevenlabs_enabled = False
        settings.elevenlabs_api_key = ""
        settings.elevenlabs_voice_id = ""
        settings.direct_initial_greeting_enabled = True
        settings.direct_initial_greeting_text = "Здравствуйте"

        sm = DirectSessionManager()
        bridge = _AudioBridge()
        telephony = BridgeTelephonyAdapter(bridge=bridge)
        voice = _VoiceProvider()

        with patch("app.integrations.direct.session_manager.GeminiLiveClient", new=MockGeminiLiveClient):
            sid = await sm.create_session(
                call_id=uuid.uuid4(),
                phone="+79991234011",
                telephony=telephony,
                voice=voice,
                session_factory=test_session_factory,
                system_prompt="test",
            )

        session = sm.get_session(sid)
        assert session is not None
        await asyncio.sleep(0.05)
        assert session.gemini_client is not None
        assert session.gemini_client.injected_instructions
        assert "Здравствуйте" in session.gemini_client.injected_instructions[0]
        await sm.terminate_session(sid)
    finally:
        settings.direct_voice_strategy = old_voice_strategy
        settings.gemini_audio_output_enabled = old_gem_audio
        settings.elevenlabs_enabled = old_el_enabled
        settings.elevenlabs_api_key = old_el_key
        settings.elevenlabs_voice_id = old_el_voice
        settings.direct_initial_greeting_enabled = old_greeting_enabled
        settings.direct_initial_greeting_text = old_greeting_text


@pytest.mark.anyio
async def test_gemini_primary_routes_assistant_audio_through_gemini_native(test_session_factory):
    old_voice_strategy = settings.direct_voice_strategy
    old_gem_audio = settings.gemini_audio_output_enabled
    old_el_enabled = settings.elevenlabs_enabled
    old_el_key = settings.elevenlabs_api_key
    old_el_voice = settings.elevenlabs_voice_id
    old_greeting_enabled = settings.direct_initial_greeting_enabled
    try:
        settings.direct_voice_strategy = "gemini_primary"
        settings.gemini_audio_output_enabled = True
        settings.elevenlabs_enabled = False
        settings.elevenlabs_api_key = ""
        settings.elevenlabs_voice_id = ""
        settings.direct_initial_greeting_enabled = False

        sm = DirectSessionManager()
        bridge = _AudioBridge()
        telephony = BridgeTelephonyAdapter(bridge=bridge)
        voice = _VoiceProvider()

        with patch("app.integrations.direct.session_manager.GeminiLiveClient", new=MockGeminiLiveClient):
            sid = await sm.create_session(
                call_id=uuid.uuid4(),
                phone="+79991234014",
                telephony=telephony,
                voice=voice,
                session_factory=test_session_factory,
                system_prompt="test",
            )

        try:
            session = sm.get_session(sid)
            assert session is not None
            assert session.voice_state is not None
            assert session.voice_state.active_path == "gemini_native"
            session.gemini_client.simulate_audio(b"\x44" * 640)
            await asyncio.sleep(0.05)
            assert bridge.played
            assert bridge.played[-1] == _resample_pcm16(b"\x44" * 640, 24000, 16000)
        finally:
            await sm.terminate_session(sid)
    finally:
        settings.direct_voice_strategy = old_voice_strategy
        settings.gemini_audio_output_enabled = old_gem_audio
        settings.elevenlabs_enabled = old_el_enabled
        settings.elevenlabs_api_key = old_el_key
        settings.elevenlabs_voice_id = old_el_voice
        settings.direct_initial_greeting_enabled = old_greeting_enabled


@pytest.mark.anyio
async def test_gemini_primary_timeout_can_activate_tts_fallback(test_session_factory):
    old_voice_strategy = settings.direct_voice_strategy
    old_fallback = settings.direct_voice_allow_tts_fallback
    old_gem_audio = settings.gemini_audio_output_enabled
    old_el_enabled = settings.elevenlabs_enabled
    old_el_key = settings.elevenlabs_api_key
    old_el_voice = settings.elevenlabs_voice_id
    old_greeting_enabled = settings.direct_initial_greeting_enabled
    old_greeting_text = settings.direct_initial_greeting_text
    old_resp_timeout = settings.direct_model_response_timeout_seconds
    try:
        settings.direct_voice_strategy = "gemini_primary"
        settings.direct_voice_allow_tts_fallback = True
        settings.gemini_audio_output_enabled = True
        settings.elevenlabs_enabled = True
        settings.elevenlabs_api_key = "k"
        settings.elevenlabs_voice_id = "v"
        settings.direct_initial_greeting_enabled = True
        settings.direct_initial_greeting_text = "Здравствуйте"
        settings.direct_model_response_timeout_seconds = 0.05

        sm = DirectSessionManager()
        bridge = _AudioBridge()
        telephony = BridgeTelephonyAdapter(bridge=bridge)
        voice = _VoiceProvider()

        with patch("app.integrations.direct.session_manager.GeminiLiveClient", new=MockGeminiLiveClient):
            sid = await sm.create_session(
                call_id=uuid.uuid4(),
                phone="+79991234015",
                telephony=telephony,
                voice=voice,
                session_factory=test_session_factory,
                system_prompt="test",
            )

        await asyncio.sleep(0.2)
        session = sm.get_session(sid)
        assert session is not None
        assert session.voice_state is not None
        assert session.voice_state.active_path == "tts_fallback"
        session.gemini_client.simulate_text("assistant", "Привет после fallback")
        await asyncio.sleep(0.05)
        assert bridge.played, "Fallback TTS audio must be delivered after activation"
        await sm.terminate_session(sid)
    finally:
        settings.direct_voice_strategy = old_voice_strategy
        settings.direct_voice_allow_tts_fallback = old_fallback
        settings.gemini_audio_output_enabled = old_gem_audio
        settings.elevenlabs_enabled = old_el_enabled
        settings.elevenlabs_api_key = old_el_key
        settings.elevenlabs_voice_id = old_el_voice
        settings.direct_initial_greeting_enabled = old_greeting_enabled
        settings.direct_initial_greeting_text = old_greeting_text
        settings.direct_model_response_timeout_seconds = old_resp_timeout


@pytest.mark.anyio
async def test_cleanup_cancels_bridge_reader_and_tts_tasks(test_session_factory):
    old_el_enabled = settings.elevenlabs_enabled
    old_el_key = settings.elevenlabs_api_key
    old_el_voice = settings.elevenlabs_voice_id
    old_voice_strategy = settings.direct_voice_strategy
    try:
        settings.direct_voice_strategy = "tts_primary"
        settings.elevenlabs_enabled = True
        settings.elevenlabs_api_key = "k"
        settings.elevenlabs_voice_id = "v"
        sm = DirectSessionManager()
        bridge = _AudioBridge()
        telephony = BridgeTelephonyAdapter(bridge=bridge)
        voice = _VoiceProvider()

        with patch("app.integrations.direct.session_manager.GeminiLiveClient", new=MockGeminiLiveClient):
            sid = await sm.create_session(
                call_id=uuid.uuid4(),
                phone="+79991234570",
                telephony=telephony,
                voice=voice,
                session_factory=test_session_factory,
                system_prompt="test",
            )
        session = sm.get_session(sid)
        assert session is not None
        session.gemini_client.simulate_text("assistant", "A")
        await asyncio.sleep(0.01)
        await sm.terminate_session(sid)
        assert not session.tts_tasks
        assert not bridge.is_open
    finally:
        settings.direct_voice_strategy = old_voice_strategy
        settings.elevenlabs_enabled = old_el_enabled
        settings.elevenlabs_api_key = old_el_key
        settings.elevenlabs_voice_id = old_el_voice


@pytest.mark.anyio
async def test_tts_failure_terminates_call_with_failed_status(test_session_factory):
    old_gem_audio = settings.gemini_audio_output_enabled
    old_el_enabled = settings.elevenlabs_enabled
    old_el_key = settings.elevenlabs_api_key
    old_el_voice = settings.elevenlabs_voice_id
    old_greeting_enabled = settings.direct_initial_greeting_enabled
    old_greeting_text = settings.direct_initial_greeting_text
    old_voice_strategy = settings.direct_voice_strategy
    call_id = uuid.uuid4()
    try:
        settings.direct_voice_strategy = "tts_primary"
        settings.gemini_audio_output_enabled = False
        settings.elevenlabs_enabled = True
        settings.elevenlabs_api_key = "k"
        settings.elevenlabs_voice_id = "v"
        settings.direct_initial_greeting_enabled = True
        settings.direct_initial_greeting_text = "Здравствуйте"
        await _create_call_record(test_session_factory, call_id, phone="+79991234012")

        sm = DirectSessionManager()
        bridge = _AudioBridge()
        telephony = BridgeTelephonyAdapter(bridge=bridge)
        voice = _FailingVoiceProvider()

        with patch("app.integrations.direct.session_manager.GeminiLiveClient", new=MockGeminiLiveClient):
            sid = await sm.create_session(
                call_id=call_id,
                phone="+79991234012",
                telephony=telephony,
                voice=voice,
                session_factory=test_session_factory,
                system_prompt="test",
            )

        session = sm.get_session(sid)
        assert session is not None
        assert session.gemini_client is not None
        session.gemini_client.simulate_text("assistant", "Здравствуйте")
        await asyncio.sleep(0.2)
        assert sm.get_session(sid) is None
        assert await _get_call_status(test_session_factory, call_id) == CallStatus.FAILED
    finally:
        settings.direct_voice_strategy = old_voice_strategy
        settings.gemini_audio_output_enabled = old_gem_audio
        settings.elevenlabs_enabled = old_el_enabled
        settings.elevenlabs_api_key = old_el_key
        settings.elevenlabs_voice_id = old_el_voice
        settings.direct_initial_greeting_enabled = old_greeting_enabled
        settings.direct_initial_greeting_text = old_greeting_text


@pytest.mark.anyio
async def test_gemini_response_timeout_terminates_call_with_failed_status(test_session_factory):
    old_gem_audio = settings.gemini_audio_output_enabled
    old_gem_audio_in = settings.gemini_audio_input_enabled
    old_el_enabled = settings.elevenlabs_enabled
    old_el_key = settings.elevenlabs_api_key
    old_el_voice = settings.elevenlabs_voice_id
    old_greeting_enabled = settings.direct_initial_greeting_enabled
    old_greeting_text = settings.direct_initial_greeting_text
    old_resp_timeout = settings.direct_model_response_timeout_seconds
    old_voice_strategy = settings.direct_voice_strategy
    call_id = uuid.uuid4()
    try:
        settings.direct_voice_strategy = "gemini_primary"
        settings.gemini_audio_output_enabled = True
        settings.gemini_audio_input_enabled = True
        settings.elevenlabs_enabled = False
        settings.elevenlabs_api_key = ""
        settings.elevenlabs_voice_id = ""
        settings.direct_initial_greeting_enabled = True
        settings.direct_initial_greeting_text = "Здравствуйте"
        settings.direct_model_response_timeout_seconds = 0.05
        await _create_call_record(test_session_factory, call_id, phone="+79991234013")

        sm = DirectSessionManager()
        bridge = _AudioBridge()
        telephony = BridgeTelephonyAdapter(bridge=bridge)
        voice = _VoiceProvider()

        with patch("app.integrations.direct.session_manager.GeminiLiveClient", new=MockGeminiLiveClient):
            sid = await sm.create_session(
                call_id=call_id,
                phone="+79991234013",
                telephony=telephony,
                voice=voice,
                session_factory=test_session_factory,
                system_prompt="test",
            )

        await asyncio.sleep(0.2)
        assert sm.get_session(sid) is None
        assert await _get_call_status(test_session_factory, call_id) == CallStatus.FAILED
    finally:
        settings.direct_voice_strategy = old_voice_strategy
        settings.gemini_audio_output_enabled = old_gem_audio
        settings.gemini_audio_input_enabled = old_gem_audio_in
        settings.elevenlabs_enabled = old_el_enabled
        settings.elevenlabs_api_key = old_el_key
        settings.elevenlabs_voice_id = old_el_voice
        settings.direct_initial_greeting_enabled = old_greeting_enabled
        settings.direct_initial_greeting_text = old_greeting_text
        settings.direct_model_response_timeout_seconds = old_resp_timeout


@pytest.mark.anyio
async def test_gemini_timeout_does_not_terminate_tts_output_path(test_session_factory):
    old_gem_audio = settings.gemini_audio_output_enabled
    old_gem_audio_in = settings.gemini_audio_input_enabled
    old_el_enabled = settings.elevenlabs_enabled
    old_el_key = settings.elevenlabs_api_key
    old_el_voice = settings.elevenlabs_voice_id
    old_greeting_enabled = settings.direct_initial_greeting_enabled
    old_greeting_text = settings.direct_initial_greeting_text
    old_resp_timeout = settings.direct_model_response_timeout_seconds
    old_voice_strategy = settings.direct_voice_strategy
    old_allow_tts_fallback = settings.direct_voice_allow_tts_fallback
    call_id = uuid.uuid4()
    try:
        settings.direct_voice_strategy = "gemini_primary"
        settings.gemini_audio_output_enabled = False
        settings.gemini_audio_input_enabled = True
        settings.elevenlabs_enabled = True
        settings.elevenlabs_api_key = "k"
        settings.elevenlabs_voice_id = "v"
        settings.direct_voice_allow_tts_fallback = True
        settings.direct_initial_greeting_enabled = True
        settings.direct_initial_greeting_text = "Здравствуйте"
        settings.direct_model_response_timeout_seconds = 0.05
        await _create_call_record(test_session_factory, call_id, phone="+79991234014")

        sm = DirectSessionManager()
        bridge = _AudioBridge()
        telephony = BridgeTelephonyAdapter(bridge=bridge)
        voice = _VoiceProvider()

        with patch("app.integrations.direct.session_manager.GeminiLiveClient", new=MockGeminiLiveClient):
            sid = await sm.create_session(
                call_id=call_id,
                phone="+79991234014",
                telephony=telephony,
                voice=voice,
                session_factory=test_session_factory,
                system_prompt="test",
            )

        session = sm.get_session(sid)
        assert session is not None
        assert session.voice_state is not None
        assert session.voice_state.active_path == "tts_fallback"

        session.metrics.awaiting_model_response = True
        session.metrics.model_turn_active = True
        session.metrics.last_model_request_at = 0.0
        await sm._check_model_response_timeout(session)

        assert sm.get_session(sid) is session
        assert session.current_status == CallStatus.IN_PROGRESS
        assert not session.metrics.awaiting_model_response
        assert not session.metrics.model_turn_active
        assert session.metrics.last_model_request_at is None
        assert await _get_call_status(test_session_factory, call_id) == CallStatus.IN_PROGRESS

        await sm.terminate_session(sid)
    finally:
        settings.direct_voice_strategy = old_voice_strategy
        settings.gemini_audio_output_enabled = old_gem_audio
        settings.gemini_audio_input_enabled = old_gem_audio_in
        settings.elevenlabs_enabled = old_el_enabled
        settings.elevenlabs_api_key = old_el_key
        settings.elevenlabs_voice_id = old_el_voice
        settings.direct_voice_allow_tts_fallback = old_allow_tts_fallback
        settings.direct_initial_greeting_enabled = old_greeting_enabled
        settings.direct_initial_greeting_text = old_greeting_text
        settings.direct_model_response_timeout_seconds = old_resp_timeout


async def test_bridge_reader_exception_triggers_auto_terminate(test_session_factory):
    old_el_enabled = settings.elevenlabs_enabled
    old_el_key = settings.elevenlabs_api_key
    old_el_voice = settings.elevenlabs_voice_id
    old_greeting_enabled = settings.direct_initial_greeting_enabled
    old_voice_strategy = settings.direct_voice_strategy
    call_id = uuid.uuid4()
    try:
        settings.direct_voice_strategy = "tts_primary"
        settings.elevenlabs_enabled = True
        settings.elevenlabs_api_key = "k"
        settings.elevenlabs_voice_id = "v"
        settings.direct_initial_greeting_enabled = False
        await _create_call_record(test_session_factory, call_id, phone="+79991234001")

        sm = DirectSessionManager()
        telephony = BridgeTelephonyAdapter(bridge=_FailingAudioBridge())
        voice = _VoiceProvider()
        with patch("app.integrations.direct.session_manager.GeminiLiveClient", new=MockGeminiLiveClient):
            sid = await sm.create_session(
                call_id=call_id,
                phone="+79991234001",
                telephony=telephony,
                voice=voice,
                session_factory=test_session_factory,
                system_prompt="test",
            )

        await asyncio.sleep(0.2)
        assert sm.get_session(sid) is None
        assert sm.active_count() == 0
        assert await _get_call_status(test_session_factory, call_id) == CallStatus.FAILED
    finally:
        settings.direct_voice_strategy = old_voice_strategy
        settings.elevenlabs_enabled = old_el_enabled
        settings.elevenlabs_api_key = old_el_key
        settings.elevenlabs_voice_id = old_el_voice
        settings.direct_initial_greeting_enabled = old_greeting_enabled


@pytest.mark.anyio
async def test_stress_sequential_10_sessions_no_leak(test_session_factory):
    old_el_enabled = settings.elevenlabs_enabled
    old_el_key = settings.elevenlabs_api_key
    old_el_voice = settings.elevenlabs_voice_id
    old_greeting_enabled = settings.direct_initial_greeting_enabled
    old_voice_strategy = settings.direct_voice_strategy
    try:
        settings.direct_voice_strategy = "tts_primary"
        settings.elevenlabs_enabled = True
        settings.elevenlabs_api_key = "k"
        settings.elevenlabs_voice_id = "v"
        settings.direct_initial_greeting_enabled = False
        sm = DirectSessionManager()
        voice = _VoiceProvider()

        with patch("app.integrations.direct.session_manager.GeminiLiveClient", new=MockGeminiLiveClient):
            for idx in range(10):
                bridge = _AudioBridge()
                telephony = BridgeTelephonyAdapter(bridge=bridge)
                sid = await sm.create_session(
                    call_id=uuid.uuid4(),
                    phone=f"+7999000{idx:04d}",
                    telephony=telephony,
                    voice=voice,
                    session_factory=test_session_factory,
                    system_prompt="test",
                )
                await bridge.stop()
                await asyncio.sleep(0.05)
                assert sm.get_session(sid) is None

        assert sm.active_count() == 0
    finally:
        settings.direct_voice_strategy = old_voice_strategy
        settings.elevenlabs_enabled = old_el_enabled
        settings.elevenlabs_api_key = old_el_key
        settings.elevenlabs_voice_id = old_el_voice
        settings.direct_initial_greeting_enabled = old_greeting_enabled


@pytest.mark.anyio
async def test_stress_parallel_5_sessions_no_leak(test_session_factory):
    old_el_enabled = settings.elevenlabs_enabled
    old_el_key = settings.elevenlabs_api_key
    old_el_voice = settings.elevenlabs_voice_id
    old_greeting_enabled = settings.direct_initial_greeting_enabled
    old_voice_strategy = settings.direct_voice_strategy
    try:
        settings.direct_voice_strategy = "tts_primary"
        settings.elevenlabs_enabled = True
        settings.elevenlabs_api_key = "k"
        settings.elevenlabs_voice_id = "v"
        settings.direct_initial_greeting_enabled = False
        sm = DirectSessionManager()
        voice = _VoiceProvider()

        async def _run_one(i: int) -> None:
            bridge = _AudioBridge()
            telephony = BridgeTelephonyAdapter(bridge=bridge)
            sid = await sm.create_session(
                call_id=uuid.uuid4(),
                phone=f"+7999555{i:04d}",
                telephony=telephony,
                voice=voice,
                session_factory=test_session_factory,
                system_prompt="test",
            )
            await bridge.stop()
            await asyncio.sleep(0.05)
            assert sm.get_session(sid) is None

        with patch("app.integrations.direct.session_manager.GeminiLiveClient", new=MockGeminiLiveClient):
            await asyncio.gather(*(_run_one(i) for i in range(5)))

        assert sm.active_count() == 0
    finally:
        settings.direct_voice_strategy = old_voice_strategy
        settings.elevenlabs_enabled = old_el_enabled
        settings.elevenlabs_api_key = old_el_key
        settings.elevenlabs_voice_id = old_el_voice
        settings.direct_initial_greeting_enabled = old_greeting_enabled


@pytest.mark.anyio
async def test_bridge_stream_close_triggers_session_termination(test_session_factory):
    old_gem_audio_in = settings.gemini_audio_input_enabled
    old_el_enabled = settings.elevenlabs_enabled
    old_el_key = settings.elevenlabs_api_key
    old_el_voice = settings.elevenlabs_voice_id
    old_greeting_enabled = settings.direct_initial_greeting_enabled
    old_voice_strategy = settings.direct_voice_strategy
    try:
        settings.direct_voice_strategy = "tts_primary"
        settings.gemini_audio_input_enabled = True
        settings.elevenlabs_enabled = True
        settings.elevenlabs_api_key = "k"
        settings.elevenlabs_voice_id = "v"
        settings.direct_initial_greeting_enabled = False
        sm = DirectSessionManager()
        bridge = _AudioBridge()
        telephony = BridgeTelephonyAdapter(bridge=bridge)
        voice = _VoiceProvider()
        with patch("app.integrations.direct.session_manager.GeminiLiveClient", new=MockGeminiLiveClient):
            sid = await sm.create_session(
                call_id=uuid.uuid4(),
                phone="+79990001003",
                telephony=telephony,
                voice=voice,
                session_factory=test_session_factory,
                system_prompt="test",
            )
        await bridge.stop()
        await asyncio.sleep(0.2)
        assert sm.get_session(sid) is None
    finally:
        settings.direct_voice_strategy = old_voice_strategy
        settings.gemini_audio_input_enabled = old_gem_audio_in
        settings.elevenlabs_enabled = old_el_enabled
        settings.elevenlabs_api_key = old_el_key
        settings.elevenlabs_voice_id = old_el_voice
        settings.direct_initial_greeting_enabled = old_greeting_enabled

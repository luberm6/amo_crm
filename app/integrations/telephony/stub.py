"""
StubTelephonyAdapter — dev/test implementation with no real telephony.

All operations are logged and return synthetic data.
audio_stream() yields silence to keep Gemini Live sessions alive.
inject_audio() allows test code to simulate incoming customer audio.

Use this adapter in:
  - Local development (no Mango credentials needed)
  - Unit/integration tests
  - CI/CD pipeline

For production use MangoTelephonyAdapter.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, AsyncIterator, Optional

from app.core.logging import get_logger
from app.integrations.telephony.base import (
    AbstractTelephonyAdapter,
    TelephonyChannel,
    TelephonyLegState,
    TelephonyOriginateResult,
)
from app.integrations.telephony.capabilities import ProviderCapabilities

if TYPE_CHECKING:
    from app.integrations.telephony.audio_bridge import AbstractAudioBridge

log = get_logger(__name__)

# 20ms silence @ 16kHz, mono, 16bit = 16000 * 2 * 0.020 = 640 bytes
_SILENCE_CHUNK = b"\x00" * 640
_STREAM_INTERVAL = 0.02  # seconds


class StubTelephonyAdapter(AbstractTelephonyAdapter):
    """
    Stub implementation for development and testing.
    Does not make real phone calls. All operations succeed silently.
    """

    def __init__(self) -> None:
        # Audio inject queues (per channel_id) — for test simulation
        self._inject_queues: dict = {}
        # Synthetic leg states for originate_call tracking
        self._leg_states: dict = {}

    # ── Capability declaration ────────────────────────────────────────────────

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_name="stub",
            supports_outbound_call=True,
            supports_audio_stream=True,           # yields silence (20ms chunks)
            supports_bridge=True,
            supports_whisper=True,
            supports_call_recording_events=False,  # no real events fired
            supports_sip_trunk=False,
            supports_real_time_events=False,
            supports_audio_bridge=True,           # returns SilenceAudioBridge
            notes="Development/test only. All operations succeed silently.",
        )

    # ── Audio channel API ─────────────────────────────────────────────────────

    async def connect(self, phone: str) -> TelephonyChannel:
        channel = TelephonyChannel(
            channel_id=f"stub-tel-{phone}",
            phone=phone,
            sip_call_id=f"stub-sip-{phone}@localhost",
            provider_leg_id=f"stub-leg-{phone}",
            state=TelephonyLegState.ANSWERED,
            metadata={"adapter": "stub"},
        )
        self._inject_queues[channel.channel_id] = asyncio.Queue()
        self._leg_states[channel.provider_leg_id] = TelephonyLegState.ANSWERED
        log.info(
            "stub_telephony.connect",
            phone=phone,
            channel_id=channel.channel_id,
            note="no real call initiated",
        )
        return channel

    async def disconnect(self, phone: str) -> None:
        log.info("stub_telephony.disconnect", phone=phone)
        channel_id = f"stub-tel-{phone}"
        leg_id = f"stub-leg-{phone}"
        self._inject_queues.pop(channel_id, None)
        self._leg_states[leg_id] = TelephonyLegState.TERMINATED

    async def audio_stream(
        self, channel: TelephonyChannel
    ) -> AsyncIterator[bytes]:
        """
        Infinite silence stream at 20ms intervals.
        Terminates when channel is disconnected (removed from _inject_queues).
        """
        channel_id = channel.channel_id
        while channel_id in self._inject_queues:
            queue = self._inject_queues.get(channel_id)
            if queue is not None and not queue.empty():
                # Return injected audio first (for tests)
                yield queue.get_nowait()
            else:
                yield _SILENCE_CHUNK
            await asyncio.sleep(_STREAM_INTERVAL)

    async def send_audio(
        self, channel: TelephonyChannel, pcm_bytes: bytes
    ) -> None:
        log.debug(
            "stub_telephony.send_audio",
            channel_id=channel.channel_id,
            bytes_len=len(pcm_bytes),
            note="stub discards audio",
        )

    async def attach_audio_bridge(
        self, channel: TelephonyChannel
    ) -> "AbstractAudioBridge":
        """Attach a SilenceAudioBridge for this channel."""
        from app.integrations.telephony.audio_bridge import SilenceAudioBridge
        bridge = SilenceAudioBridge()
        await bridge.open(channel)
        log.debug(
            "stub_telephony.attach_audio_bridge",
            channel_id=channel.channel_id,
        )
        return bridge

    async def detach_audio_bridge(self, bridge: "AbstractAudioBridge") -> None:
        """Detach and close the audio bridge."""
        await bridge.close()
        log.debug("stub_telephony.detach_audio_bridge")

    # ── Telephony control API ─────────────────────────────────────────────────

    async def originate_call(
        self,
        phone: str,
        caller_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> TelephonyOriginateResult:
        leg_id = f"stub-leg-{phone}"
        self._leg_states[leg_id] = TelephonyLegState.RINGING
        log.info(
            "stub_telephony.originate_call",
            phone=phone,
            caller_id=caller_id,
            leg_id=leg_id,
            note="no real call originated",
        )
        return TelephonyOriginateResult(
            leg_id=leg_id,
            sip_call_id=f"stub-sip-{phone}@localhost",
            provider_response={"status": "stub_ok"},
        )

    async def bridge_legs(
        self,
        customer_leg_id: str,
        manager_leg_id: str,
    ) -> None:
        log.info(
            "stub_telephony.bridge_legs",
            customer_leg=customer_leg_id,
            manager_leg=manager_leg_id,
            note="stub bridge noop",
        )
        self._leg_states[customer_leg_id] = TelephonyLegState.BRIDGED
        self._leg_states[manager_leg_id] = TelephonyLegState.BRIDGED

    async def play_whisper(self, leg_id: str, message: str) -> None:
        log.info(
            "stub_telephony.play_whisper",
            leg_id=leg_id,
            message_preview=message[:80],
            note="stub discards whisper",
        )

    async def terminate_leg(self, leg_id: str) -> None:
        log.info("stub_telephony.terminate_leg", leg_id=leg_id)
        self._leg_states[leg_id] = TelephonyLegState.TERMINATED

    async def get_leg_state(self, leg_id: str) -> TelephonyLegState:
        return self._leg_states.get(leg_id, TelephonyLegState.TERMINATED)

    # ── Test helpers ──────────────────────────────────────────────────────────

    def inject_audio(self, channel_id: str, pcm_bytes: bytes) -> None:
        """
        Test helper: inject a PCM chunk into the audio stream.
        The next yield from audio_stream() returns this chunk.
        """
        queue = self._inject_queues.get(channel_id)
        if queue is not None:
            queue.put_nowait(pcm_bytes)

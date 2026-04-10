"""
AudioBridge — session-scoped media plane for Direct mode.

Separates audio session lifecycle (PCM in/out) from telephony adapter control plane.
Allows multiple bridge implementations (Silence for dev, SIP RTP for production, etc.)
without scattering audio buffer state across the adapter.

Design:
  - open(channel) — establish audio session
  - close() — terminate gracefully
  - audio_in() — async generator: customer PCM → Gemini (16kHz mono 16bit)
  - audio_out(pcm) — Gemini PCM → customer
  - is_open property — for polling

Implementations:
  - SilenceAudioBridge: yields silence for dev/test
  - NullAudioBridge: audio_in() terminates immediately (provider has no audio capability)
  - FreeSwitchAudioBridge (future): real RTP via SIP trunk
"""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional

from app.core.logging import get_logger
from app.integrations.telephony.base import TelephonyChannel

log = get_logger(__name__)

# PCM silence: 16kHz, mono, 16bit, 20ms = 640 bytes
_SILENCE_CHUNK = b"\x00" * 640


class AbstractAudioBridge(ABC):
    """
    Session-scoped audio bridge for a single call leg.

    The bridge owns the media lifecycle: open → stream → close.
    Caller owns lifecycle management; bridge owns buffering/timing.
    """

    @abstractmethod
    async def open(self, channel: TelephonyChannel) -> None:
        """
        Establish audio session for this channel.
        May be a no-op for stubs, but must not raise for valid channels.
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Close and release the audio session. Idempotent."""
        ...

    @property
    @abstractmethod
    def is_open(self) -> bool:
        """Return True if audio session is active."""
        ...

    @abstractmethod
    async def audio_in(self) -> AsyncIterator[bytes]:
        """
        Async generator: yield PCM chunks from customer (16kHz mono 16bit).
        Yields continuously while audio is flowing.
        Terminates when customer hangs up or close() is called.
        """
        ...

    @abstractmethod
    async def audio_out(self, pcm: bytes) -> None:
        """
        Send PCM audio to the customer.
        pcm — 16kHz mono 16bit (or other format if adapter handles resampling).
        """
        ...


class SilenceAudioBridge(AbstractAudioBridge):
    """
    Development/test bridge: yields silence indefinitely until close().

    audio_in(): async for loop yields 640 bytes (20ms @16kHz) every 20ms
    audio_out(): accepts bytes, logs at DEBUG (no spam)
    """

    def __init__(self) -> None:
        self._is_open = False
        self._close_event = asyncio.Event()
        self._test_audio_queue: asyncio.Queue = asyncio.Queue()

    async def open(self, channel: TelephonyChannel) -> None:
        self._is_open = True
        log.debug("silence_bridge.opened", channel_id=channel.channel_id)

    async def close(self) -> None:
        if not self._is_open:
            return
        self._is_open = False
        self._close_event.set()
        log.debug("silence_bridge.closed")

    @property
    def is_open(self) -> bool:
        return self._is_open

    async def audio_in(self) -> AsyncIterator[bytes]:
        """Yield silence every 20ms until close() is called."""
        try:
            while self._is_open:
                # Check for test-injected audio first
                try:
                    chunk = self._test_audio_queue.get_nowait()
                    yield chunk
                except asyncio.QueueEmpty:
                    # No test audio, yield silence
                    yield _SILENCE_CHUNK
                    await asyncio.sleep(0.020)  # 20ms timing
        finally:
            pass

    async def audio_out(self, pcm: bytes) -> None:
        """Accept audio (silent for test)."""
        log.debug("silence_bridge.audio_out", bytes_len=len(pcm))

    def inject_audio(self, pcm: bytes) -> None:
        """Test helper: inject PCM directly into audio_in stream."""
        try:
            self._test_audio_queue.put_nowait(pcm)
        except asyncio.QueueFull:
            pass


class NullAudioBridge(AbstractAudioBridge):
    """
    Honest placeholder when provider has no audio capability.

    Used by Mango/Twilio until SIP UA is available.
    Allows session to stay alive for text steering, but no audio flow.

    audio_in(): terminates immediately (0 chunks yielded)
    audio_out(): logs WARNING + discards
    """

    def __init__(self) -> None:
        self._is_open = False

    async def open(self, channel: TelephonyChannel) -> None:
        self._is_open = True
        log.debug("null_bridge.opened", channel_id=channel.channel_id)

    async def close(self) -> None:
        self._is_open = False
        log.debug("null_bridge.closed")

    @property
    def is_open(self) -> bool:
        return self._is_open

    async def audio_in(self) -> AsyncIterator[bytes]:
        """Terminate immediately — no audio available."""
        return
        # The yield is unreachable but needed for type checker (async generator)
        yield  # type: ignore[unreachable]

    async def audio_out(self, pcm: bytes) -> None:
        """Log WARNING + discard."""
        log.warning(
            "null_bridge.audio_out.unsupported",
            bytes_len=len(pcm),
            note="Telephony provider does not support audio streaming. Session audio-only.",
        )

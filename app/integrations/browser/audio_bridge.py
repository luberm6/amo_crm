from __future__ import annotations

import asyncio
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import AsyncIterator, Optional

from app.core.logging import get_logger
from app.integrations.telephony.audio_bridge import AbstractAudioBridge
from app.integrations.telephony.base import TelephonyChannel

log = get_logger(__name__)


@dataclass
class BrowserBridgeSnapshot:
    call_id: str
    token: str
    session_id: Optional[str]
    agent_id: Optional[str]
    voice_strategy: Optional[str]
    active_voice_path: Optional[str]
    is_open: bool
    client_connected: bool
    created_at: float
    last_client_event_at: Optional[float]
    inbound_chunks: int
    outbound_chunks: int
    last_disconnect_reason: Optional[str]


class BrowserAudioBridge(AbstractAudioBridge):
    """
    In-memory audio bridge for browser QA sessions.

    Browser microphone PCM -> inbound queue -> DirectSessionManager -> Gemini
    DirectSessionManager/voice output -> outbound queue -> browser speaker
    """

    def __init__(self, call_id: uuid.UUID) -> None:
        self.call_id = call_id
        self.token = secrets.token_urlsafe(24)
        self.session_id: Optional[str] = None
        self.agent_id: Optional[str] = None
        self.voice_strategy: Optional[str] = None
        self.active_voice_path: Optional[str] = None
        self._is_open = False
        self._client_connected = False
        self._created_at = time.time()
        self._last_client_event_at: Optional[float] = None
        self._inbound_queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue(maxsize=128)
        self._outbound_queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue(maxsize=128)
        self._close_event = asyncio.Event()
        self._inbound_chunks = 0
        self._outbound_chunks = 0
        self._control_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=16)
        self.hangup_reason: Optional[str] = None
        self.last_disconnect_reason: Optional[str] = None

    async def open(self, channel: TelephonyChannel) -> None:
        self._is_open = True
        if channel.metadata is None:
            channel.metadata = {}
        channel.metadata["browser_token"] = self.token
        channel.metadata["browser_call_id"] = str(self.call_id)
        log.info(
            "browser_bridge.opened",
            call_id=str(self.call_id),
            channel_id=channel.channel_id,
            session_id=self.session_id,
            agent_id=self.agent_id,
            voice_strategy=self.voice_strategy,
            active_voice_path=self.active_voice_path,
        )

    async def close(self) -> None:
        if not self._is_open:
            return
        self._is_open = False
        self._close_event.set()
        try:
            self._inbound_queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
        try:
            self._outbound_queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
        log.info(
            "browser_bridge.closed",
            call_id=str(self.call_id),
            session_id=self.session_id,
            agent_id=self.agent_id,
            voice_strategy=self.voice_strategy,
            active_voice_path=self.active_voice_path,
            inbound_chunks=self._inbound_chunks,
            outbound_chunks=self._outbound_chunks,
            disconnect_reason=self.last_disconnect_reason,
        )

    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def client_connected(self) -> bool:
        return self._client_connected

    async def audio_in(self) -> AsyncIterator[bytes]:
        while self._is_open or not self._inbound_queue.empty():
            if not self._is_open and self._inbound_queue.empty():
                break
            try:
                chunk = await asyncio.wait_for(self._inbound_queue.get(), timeout=0.25)
            except asyncio.TimeoutError:
                if self._close_event.is_set():
                    break
                continue
            if chunk is None:
                return
            yield chunk

    async def audio_out(self, pcm: bytes) -> None:
        if not self._is_open or not pcm:
            return
        self._outbound_chunks += 1
        self._last_client_event_at = time.time()
        try:
            self._outbound_queue.put_nowait(pcm)
        except asyncio.QueueFull:
            try:
                self._outbound_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self._outbound_queue.put_nowait(pcm)
        if self._outbound_chunks == 1 or self._outbound_chunks % 50 == 0:
            log.info(
                "browser_bridge.audio_out",
                call_id=str(self.call_id),
                session_id=self.session_id,
                agent_id=self.agent_id,
                voice_strategy=self.voice_strategy,
                active_voice_path=self.active_voice_path,
                chunk_index=self._outbound_chunks,
                bytes_len=len(pcm),
            )

    def attach_client(self) -> None:
        if self._client_connected:
            raise RuntimeError("Browser client is already attached")
        self._client_connected = True
        self._last_client_event_at = time.time()
        log.info(
            "browser_bridge.client_attached",
            call_id=str(self.call_id),
            session_id=self.session_id,
            agent_id=self.agent_id,
            voice_strategy=self.voice_strategy,
            active_voice_path=self.active_voice_path,
        )

    async def detach_client(self, reason: str = "browser_disconnect") -> None:
        self._client_connected = False
        self.hangup_reason = reason
        self.last_disconnect_reason = reason
        self._last_client_event_at = time.time()
        await self.close()

    def annotate_runtime(
        self,
        *,
        session_id: Optional[str],
        agent_id: Optional[str],
        voice_strategy: Optional[str],
        active_voice_path: Optional[str],
    ) -> None:
        self.session_id = session_id
        self.agent_id = agent_id
        self.voice_strategy = voice_strategy
        self.active_voice_path = active_voice_path

    def send_control(self, message: dict) -> None:
        """Enqueue a control message to be sent to the browser as JSON."""
        try:
            self._control_queue.put_nowait(message)
        except asyncio.QueueFull:
            pass

    async def control_messages(self) -> AsyncIterator[dict]:
        while self._is_open:
            try:
                msg = await asyncio.wait_for(self._control_queue.get(), timeout=0.1)
                yield msg
            except asyncio.TimeoutError:
                continue

    def push_audio(self, pcm: bytes) -> None:
        if not self._is_open or not self._client_connected or not pcm:
            return
        self._inbound_chunks += 1
        self._last_client_event_at = time.time()
        try:
            self._inbound_queue.put_nowait(pcm)
        except asyncio.QueueFull:
            try:
                self._inbound_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self._inbound_queue.put_nowait(pcm)
        if self._inbound_chunks == 1 or self._inbound_chunks % 50 == 0:
            log.info(
                "browser_bridge.audio_in",
                call_id=str(self.call_id),
                session_id=self.session_id,
                agent_id=self.agent_id,
                voice_strategy=self.voice_strategy,
                active_voice_path=self.active_voice_path,
                chunk_index=self._inbound_chunks,
                bytes_len=len(pcm),
            )

    async def outbound_audio(self) -> AsyncIterator[bytes]:
        while self._is_open or not self._outbound_queue.empty():
            if not self._is_open and self._outbound_queue.empty():
                break
            try:
                chunk = await asyncio.wait_for(self._outbound_queue.get(), timeout=0.25)
            except asyncio.TimeoutError:
                if self._close_event.is_set():
                    break
                continue
            if chunk is None:
                return
            yield chunk

    def snapshot(self) -> BrowserBridgeSnapshot:
        return BrowserBridgeSnapshot(
            call_id=str(self.call_id),
            token=self.token,
            session_id=self.session_id,
            agent_id=self.agent_id,
            voice_strategy=self.voice_strategy,
            active_voice_path=self.active_voice_path,
            is_open=self._is_open,
            client_connected=self._client_connected,
            created_at=self._created_at,
            last_client_event_at=self._last_client_event_at,
            inbound_chunks=self._inbound_chunks,
            outbound_chunks=self._outbound_chunks,
            last_disconnect_reason=self.last_disconnect_reason,
        )

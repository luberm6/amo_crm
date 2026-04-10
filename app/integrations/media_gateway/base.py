from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional

from app.core.exceptions import EngineError


class MediaGatewayError(EngineError):
    error_code = "media_gateway_error"


class MediaGatewayNotReadyError(MediaGatewayError):
    error_code = "media_gateway_not_ready"
    status_code = 503


class MediaEventType(str, enum.Enum):
    AUDIO_IN = "audio_in"
    BARGE_IN = "barge_in"
    HANGUP = "hangup"
    DTMF = "dtmf"
    HEARTBEAT = "heartbeat"


@dataclass
class MediaEvent:
    type: MediaEventType
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    pcm: Optional[bytes] = None
    dtmf_digit: Optional[str] = None
    reason: Optional[str] = None
    payload: Optional[dict[str, Any]] = None


@dataclass
class MediaSessionHandle:
    session_id: str
    call_id: str
    provider_leg_id: str
    remote_sdp: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class AbstractMediaGateway(ABC):
    """
    Media gateway contract for RTP/audio plane.

    Responsibilities:
    - attach/detach media session
    - stream inbound media events to backend
    - accept outbound PCM from backend
    - propagate interruption and hangup intents
    """

    @abstractmethod
    async def attach_session(
        self,
        *,
        call_id: str,
        provider_leg_id: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> MediaSessionHandle:
        ...

    @abstractmethod
    async def detach_session(self, session_id: str) -> None:
        ...

    @abstractmethod
    async def events(self, session_id: str) -> AsyncIterator[MediaEvent]:
        ...

    @abstractmethod
    async def send_audio(self, session_id: str, pcm: bytes) -> None:
        ...

    @abstractmethod
    async def send_barge_in(self, session_id: str) -> None:
        ...

    @abstractmethod
    async def propagate_hangup(
        self,
        session_id: str,
        *,
        reason: Optional[str] = None,
    ) -> None:
        ...

    @abstractmethod
    async def health(self) -> dict[str, Any]:
        ...

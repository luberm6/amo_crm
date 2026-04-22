from __future__ import annotations

import asyncio
from typing import AsyncIterator, Optional

from app.core.logging import get_logger
from app.integrations.media_gateway.base import (
    AbstractMediaGateway,
    MediaEventType,
)
from app.integrations.telephony.audio_bridge import AbstractAudioBridge
from app.integrations.telephony.base import TelephonyChannel
from app.integrations.telephony.mango_freeswitch_correlation import (
    get_mango_freeswitch_correlation_store,
)

log = get_logger(__name__)


class FreeSwitchAudioBridge(AbstractAudioBridge):
    """
    Adapter from AbstractAudioBridge contract to media gateway session.

    This class is media-plane only and does not perform business state updates.
    """

    def __init__(self, gateway: AbstractMediaGateway) -> None:
        self._gateway = gateway
        self._is_open = False
        self._session_id: Optional[str] = None
        self._hangup_reason: Optional[str] = None
        self._barge_in_event = asyncio.Event()

    async def open(self, channel: TelephonyChannel) -> None:
        call_id = None
        if channel.metadata:
            call_id = channel.metadata.get("internal_call_id")
        if call_id is None:
            call_id = channel.channel_id
        mango_leg_id = channel.provider_leg_id or channel.channel_id
        provider_leg_id = mango_leg_id
        if mango_leg_id and str(mango_leg_id).startswith("direct-"):
            snap = await get_mango_freeswitch_correlation_store().get(str(mango_leg_id))
            if snap is not None and snap.freeswitch_uuid:
                provider_leg_id = snap.freeswitch_uuid

        handle = await self._gateway.attach_session(
            call_id=str(call_id),
            provider_leg_id=provider_leg_id,
            metadata={
                "phone": channel.phone,
                "mango_leg_id": mango_leg_id,
            },
        )
        self._session_id = handle.session_id
        self._is_open = True
        log.info(
            "freeswitch_bridge.opened",
            session_id=self._session_id,
            mango_leg_id=mango_leg_id,
            provider_leg_id=provider_leg_id,
        )

    async def close(self) -> None:
        if not self._is_open:
            return
        self._is_open = False
        if self._session_id:
            await self._gateway.detach_session(self._session_id)
        log.info("freeswitch_bridge.closed", session_id=self._session_id)

    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def hangup_reason(self) -> Optional[str]:
        return self._hangup_reason

    @property
    def barge_in_triggered(self) -> bool:
        return self._barge_in_event.is_set()

    async def audio_in(self) -> AsyncIterator[bytes]:
        if not self._is_open or not self._session_id:
            return
            yield  # type: ignore[unreachable]

        async for evt in self._gateway.events(self._session_id):
            if evt.type == MediaEventType.AUDIO_IN and evt.pcm:
                yield evt.pcm
            elif evt.type == MediaEventType.BARGE_IN:
                self._barge_in_event.set()
                log.info("freeswitch_bridge.barge_in", session_id=self._session_id)
            elif evt.type == MediaEventType.HANGUP:
                self._hangup_reason = evt.reason
                self._is_open = False
                log.info(
                    "freeswitch_bridge.hangup",
                    session_id=self._session_id,
                    reason=evt.reason,
                )
                return

    async def audio_out(self, pcm: bytes) -> None:
        if not self._is_open or not self._session_id:
            log.warning(
                "freeswitch_bridge.audio_out_ignored_closed",
                session_id=self._session_id,
                bytes=len(pcm or b""),
            )
            return
        await self._gateway.send_audio(self._session_id, pcm)

    async def send_interrupt(self) -> None:
        if not self._is_open or not self._session_id:
            log.warning(
                "freeswitch_bridge.send_interrupt_ignored_closed",
                session_id=self._session_id,
            )
            return
        await self._gateway.send_barge_in(self._session_id)

    async def propagate_hangup(self, reason: Optional[str] = None) -> None:
        if not self._is_open or not self._session_id:
            log.warning(
                "freeswitch_bridge.propagate_hangup_ignored_closed",
                session_id=self._session_id,
                reason=reason,
            )
            return
        await self._gateway.propagate_hangup(self._session_id, reason=reason)

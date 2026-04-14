from __future__ import annotations

import uuid
from typing import AsyncIterator, Optional

from app.core.logging import get_logger
from app.integrations.browser.registry import BrowserSessionRegistry
from app.integrations.telephony.base import (
    AbstractTelephonyAdapter,
    TelephonyChannel,
    TelephonyLegState,
    TelephonyOriginateResult,
)
from app.integrations.telephony.capabilities import ProviderCapabilities

log = get_logger(__name__)


class BrowserTelephonyAdapter(AbstractTelephonyAdapter):
    """
    Internal browser-only telephony adapter.

    It provides the same Direct voice lifecycle as telephony adapters, but the
    media bridge is an in-memory browser WebSocket bridge instead of Mango/PSTN.
    """

    def __init__(self, registry: BrowserSessionRegistry) -> None:
        self._registry = registry
        self._prepared_call_id: Optional[uuid.UUID] = None
        self._phone_to_call_id: dict[str, uuid.UUID] = {}

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_name="browser",
            supports_outbound_call=True,
            supports_audio_stream=True,
            supports_bridge=False,
            supports_whisper=False,
            supports_call_recording_events=False,
            supports_sip_trunk=False,
            supports_real_time_events=True,
            supports_audio_bridge=True,
            notes="Internal QA browser sandbox only.",
        )

    def prepare_browser_call(self, call_id: uuid.UUID) -> None:
        self._prepared_call_id = call_id

    async def connect(
        self,
        phone: str,
        caller_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> TelephonyChannel:
        call_id = self._prepared_call_id
        if call_id is None:
            raise RuntimeError("BrowserTelephonyAdapter.prepare_browser_call() was not called")
        self._phone_to_call_id[phone] = call_id
        self._registry.ensure_bridge(call_id)
        channel = TelephonyChannel(
            channel_id=f"browser-{call_id}",
            phone=phone,
            provider_leg_id=f"browser-leg-{call_id}",
            state=TelephonyLegState.ANSWERED,
            metadata={
                "browser_call_id": str(call_id),
                "caller_id": caller_id,
                "runtime_metadata": metadata or {},
            },
        )
        log.info("browser_telephony.connect", call_id=str(call_id), phone=phone)
        return channel

    async def disconnect(self, phone: str) -> None:
        call_id = self._phone_to_call_id.pop(phone, None)
        if call_id is None:
            return
        bridge = self._registry.get_bridge(call_id)
        if bridge is not None:
            await bridge.detach_client(reason="browser_stop")
        self._registry.remove_bridge(call_id)
        log.info("browser_telephony.disconnect", call_id=str(call_id), phone=phone)

    async def audio_stream(self, channel: TelephonyChannel) -> AsyncIterator[bytes]:
        bridge = await self.attach_audio_bridge(channel)
        async for chunk in bridge.audio_in():
            yield chunk

    async def send_audio(self, channel: TelephonyChannel, pcm_bytes: bytes) -> None:
        bridge = await self.attach_audio_bridge(channel)
        await bridge.audio_out(pcm_bytes)

    async def attach_audio_bridge(self, channel: TelephonyChannel):
        call_id = uuid.UUID(channel.metadata["browser_call_id"])
        bridge = self._registry.ensure_bridge(call_id)
        if not bridge.is_open:
            await bridge.open(channel)
        return bridge

    async def detach_audio_bridge(self, bridge) -> None:
        await bridge.close()

    async def originate_call(
        self,
        phone: str,
        caller_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> TelephonyOriginateResult:
        call_id = self._prepared_call_id
        if call_id is None:
            raise RuntimeError("BrowserTelephonyAdapter.prepare_browser_call() was not called")
        return TelephonyOriginateResult(
            leg_id=f"browser-leg-{call_id}",
            sip_call_id=f"browser-sip-{call_id}",
            provider_response={"phone": phone, "caller_id": caller_id, "metadata": metadata or {}},
        )

    async def bridge_legs(self, customer_leg_id: str, manager_leg_id: str) -> None:
        raise RuntimeError("Browser sandbox does not support transfer bridging")

    async def play_whisper(self, leg_id: str, message: str) -> None:
        raise RuntimeError("Browser sandbox does not support whisper")

    async def terminate_leg(self, leg_id: str) -> None:
        return None

    async def get_leg_state(self, leg_id: str) -> TelephonyLegState:
        return TelephonyLegState.ANSWERED

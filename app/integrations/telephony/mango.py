"""
Mango telephony control-plane adapter.

Important:
- This adapter covers telephony control only (originate/terminate/bridge/whisper/state).
- Media streaming is still NOT implemented (requires SIP bridge).
- Runtime leg state is persistent/shared via MangoLegStateStore (Redis preferred).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import TYPE_CHECKING, AsyncIterator, Optional

import httpx

from app.core.config import settings
from app.core.exceptions import EngineError
from app.core.logging import get_logger
from app.core.redis_client import get_redis
from app.integrations.media_gateway.factory import get_media_gateway
from app.integrations.telephony.base import (
    AbstractTelephonyAdapter,
    TelephonyChannel,
    TelephonyLegState,
    TelephonyOriginateResult,
)
from app.integrations.telephony.capabilities import ProviderCapabilities
from app.integrations.telephony.freeswitch_bridge import FreeSwitchAudioBridge
from app.integrations.telephony.mango_events import MangoEventProcessor
from app.integrations.telephony.mango_freeswitch_correlation import (
    AbstractMangoFreeSwitchCorrelationStore,
    get_mango_freeswitch_correlation_store,
)
from app.integrations.telephony.mango_runtime import resolve_mango_from_ext
from app.integrations.telephony.mango_state_store import (
    AbstractMangoLegStateStore,
    InMemoryMangoLegStateStore,
    RedisMangoLegStateStore,
)

if TYPE_CHECKING:
    from app.integrations.telephony.audio_bridge import AbstractAudioBridge

log = get_logger(__name__)


class TelephonyError(EngineError):
    error_code = "telephony_error"


class MangoTelephonyAdapter(AbstractTelephonyAdapter):
    _DEFAULT_BASE_URL = "https://app.mango-office.ru/vpbx"

    def __init__(
        self,
        state_store: Optional[AbstractMangoLegStateStore] = None,
        correlation_store: Optional[AbstractMangoFreeSwitchCorrelationStore] = None,
    ) -> None:
        self._api_key = settings.mango_api_key
        self._api_salt = settings.mango_api_salt
        self._from_ext = settings.mango_from_ext
        self._http = httpx.AsyncClient(
            base_url=(settings.mango_api_base_url or self._DEFAULT_BASE_URL).rstrip("/"),
            timeout=15.0,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if state_store is not None:
            self._state = state_store
        else:
            redis = get_redis()
            self._state = RedisMangoLegStateStore(redis) if redis is not None else InMemoryMangoLegStateStore()
            if redis is None:
                log.warning(
                    "mango_telephony.in_memory_state_store",
                    message="Redis unavailable, Mango leg state will not survive process restart.",
                )
        self._corr = correlation_store or get_mango_freeswitch_correlation_store()

    async def aclose(self) -> None:
        await self._http.aclose()

    @property
    def capabilities(self) -> ProviderCapabilities:
        media_bridge_supported = bool(
            settings.media_gateway_enabled
            and settings.media_gateway_provider == "freeswitch"
            and settings.media_gateway_mode in {"mock", "esl_rtp"}
        )
        return ProviderCapabilities(
            provider_name="mango",
            supports_outbound_call=True,
            supports_audio_stream=False,
            supports_bridge=True,
            supports_whisper=True,
            supports_call_recording_events=True,
            supports_sip_trunk=True,
            supports_real_time_events=True,
            supports_audio_bridge=media_bridge_supported,
            notes=(
                "Control-plane only. audio_stream/send_audio require SIP media bridge "
                "(FreeSWITCH/Asterisk). FreeSWITCH mock/esl_rtp paths are available; "
                "real-world validation is still required."
            ),
        )

    async def connect(
        self,
        phone: str,
        caller_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> TelephonyChannel:
        existing_leg_id = str(
            (metadata or {}).get("existing_leg_id")
            or (metadata or {}).get("provider_leg_id")
            or (metadata or {}).get("mango_leg_id")
            or ""
        ).strip()
        if existing_leg_id:
            log.info(
                "mango_telephony.attach_existing_leg",
                phone=phone,
                mango_leg_id=existing_leg_id,
            )
            channel = TelephonyChannel(
                channel_id=existing_leg_id,
                phone=phone,
                sip_call_id=None,
                provider_leg_id=existing_leg_id,
                state=TelephonyLegState.ANSWERED,
                metadata={
                    "internal_call_id": (metadata or {}).get("call_id"),
                    "existing_leg": True,
                    **(metadata or {}),
                },
            )
            return channel

        result = await self.originate_call(phone, caller_id=caller_id, metadata=metadata)
        answered_state = await self.wait_for_answered(result.leg_id)
        channel = TelephonyChannel(
            channel_id=result.leg_id,
            phone=phone,
            sip_call_id=result.sip_call_id,
            provider_leg_id=result.leg_id,
            state=answered_state,
            metadata=result.provider_response,
        )
        return channel

    async def disconnect(self, phone: str) -> None:
        raise NotImplementedError(
            "MangoTelephonyAdapter.disconnect(phone) is unsupported. Use terminate_leg(leg_id)."
        )

    async def audio_stream(self, channel: TelephonyChannel) -> AsyncIterator[bytes]:
        raise NotImplementedError(
            "MangoTelephonyAdapter.audio_stream() is not implemented. "
            "Mango REST API does not provide bidirectional PCM media."
        )
        yield b""

    async def send_audio(self, channel: TelephonyChannel, pcm_bytes: bytes) -> None:
        raise NotImplementedError(
            "MangoTelephonyAdapter.send_audio() is not implemented. "
            "Use SIP media bridge in a separate runtime path."
        )

    async def attach_audio_bridge(self, channel: TelephonyChannel) -> "AbstractAudioBridge":
        if (
            settings.media_gateway_enabled
            and settings.media_gateway_provider == "freeswitch"
            and settings.media_gateway_mode in {"mock", "esl_rtp"}
        ):
            bridge = FreeSwitchAudioBridge(get_media_gateway())
            await bridge.open(channel)
            return bridge

        self.capabilities.check("audio_bridge")
        raise RuntimeError("unreachable")

    async def detach_audio_bridge(self, bridge: "AbstractAudioBridge") -> None:
        await bridge.close()

    async def originate_call(
        self,
        phone: str,
        caller_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> TelephonyOriginateResult:
        resolved_from_ext = await resolve_mango_from_ext(
            explicit_from_ext=caller_id or self._from_ext,
            metadata=metadata,
        )
        from_ext = resolved_from_ext.value
        if not from_ext:
            raise TelephonyError("MANGO_FROM_EXT is not configured.")

        line_number = "0"
        if metadata:
            explicit_line = (
                metadata.get("telephony_remote_line_id")
                or metadata.get("mango_remote_line_id")
                or metadata.get("line_number")
            )
            if explicit_line:
                line_number = str(explicit_line)

        resp_data = await self._post(
            "/commands/callback",
            {
                "command_id": f"direct-{uuid.uuid4().hex}",
                "from": {
                    "extension": from_ext,
                },
                "to_number": phone,
                "line_number": line_number,
            },
        )
        call_uid = str(resp_data.get("uid", ""))
        if not call_uid:
            raise TelephonyError("Mango callback API returned no uid", detail=resp_data)

        call_id = str(metadata.get("call_id")) if metadata and metadata.get("call_id") else None
        transfer_id = str(metadata.get("transfer_id")) if metadata and metadata.get("transfer_id") else None
        role = str(metadata.get("role")) if metadata and metadata.get("role") else None
        await self._state.set_leg_state(
            call_uid,
            TelephonyLegState.INITIATING,
            call_id=call_id,
            transfer_id=transfer_id,
            role=role,
        )
        await self._corr.upsert_mapping(
            mango_leg_id=call_uid,
            call_id=call_id,
            freeswitch_uuid=call_uid,
        )

        log.info(
            "mango_telephony.call_originated",
            phone=phone,
            mango_uid=call_uid,
            line_number=line_number,
            from_ext=from_ext,
            from_ext_source=resolved_from_ext.source,
            from_ext_candidate_count=resolved_from_ext.candidate_count,
        )
        return TelephonyOriginateResult(
            leg_id=call_uid,
            sip_call_id=None,
            provider_response={
                **resp_data,
                "line_number": line_number,
                "from_extension": from_ext,
                "from_extension_source": resolved_from_ext.source,
            },
        )

    async def wait_for_answered(
        self,
        leg_id: str,
        *,
        timeout: Optional[float] = None,
    ) -> TelephonyLegState:
        wait_timeout = float(timeout or settings.mango_answer_wait_timeout_seconds)
        state = await self._state.wait_for_leg_state(
            leg_id=leg_id,
            accepted={TelephonyLegState.ANSWERED, TelephonyLegState.BRIDGED},
            failed={TelephonyLegState.FAILED, TelephonyLegState.TERMINATED},
            timeout=wait_timeout,
            poll_fallback=lambda: self._poll_answer_state_with_fallback(leg_id),
        )
        if state is None:
            raise TelephonyError(
                f"Timed out waiting for leg {leg_id} to answer after {wait_timeout}s"
            )
        if state in (TelephonyLegState.FAILED, TelephonyLegState.TERMINATED):
            raise TelephonyError(
                f"Leg {leg_id} ended before answer: {state.value}",
                detail={"leg_id": leg_id, "state": state.value},
            )
        return state

    async def bridge_legs(self, customer_leg_id: str, manager_leg_id: str) -> None:
        customer_state = await self.get_leg_state(customer_leg_id)
        manager_state = await self.get_leg_state(manager_leg_id)
        if customer_state not in (TelephonyLegState.ANSWERED, TelephonyLegState.BRIDGED):
            raise TelephonyError(
                f"Cannot bridge: customer leg {customer_leg_id} in state {customer_state.value}"
            )
        if manager_state not in (TelephonyLegState.ANSWERED, TelephonyLegState.BRIDGED):
            raise TelephonyError(
                f"Cannot bridge: manager leg {manager_leg_id} in state {manager_state.value}"
            )

        bridge_key = MangoEventProcessor.bridge_key(customer_leg_id, manager_leg_id)
        await self._state.set_bridge_status(bridge_key, "bridge_started")

        try:
            await self._post(
                "/commands/transfer",
                {
                    "call_id": customer_leg_id,
                    "to[number]": manager_leg_id,
                    "method": "blind",
                },
            )
        except Exception:
            await self._state.set_bridge_status(bridge_key, "bridge_failed")
            await self.terminate_leg(manager_leg_id)
            raise

        await self._wait_for_bridge_confirmation(customer_leg_id, manager_leg_id)
        await self._state.set_leg_state(customer_leg_id, TelephonyLegState.BRIDGED)
        await self._state.set_leg_state(manager_leg_id, TelephonyLegState.BRIDGED)
        await self._state.set_bridge_status(bridge_key, "bridge_confirmed")

    async def play_whisper(self, leg_id: str, message: str) -> None:
        state = await self.get_leg_state(leg_id)
        if state != TelephonyLegState.ANSWERED:
            await self._state.set_whisper_status(leg_id, "whisper_failed")
            raise TelephonyError(
                f"Whisper requires answered manager leg, got {state.value}",
                detail={"leg_id": leg_id, "state": state.value},
            )

        await self._state.set_whisper_status(leg_id, "whisper_started")
        try:
            await self._post(
                "/commands/play",
                {
                    "call_id": leg_id,
                    "file": f"tts:{message[:500]}",
                },
            )
        except Exception:
            await self._state.set_whisper_status(leg_id, "whisper_failed")
            raise

        status = await self._state.wait_for_whisper_status(
            leg_id=leg_id,
            accepted={"whisper_finished"},
            failed={"whisper_failed"},
            timeout=float(settings.mango_whisper_confirm_timeout_seconds),
        )
        if status != "whisper_finished":
            await self._state.set_whisper_status(leg_id, "whisper_failed")
            raise TelephonyError(
                "Whisper confirmation timed out or failed.",
                detail={"leg_id": leg_id, "status": status},
            )

    async def terminate_leg(self, leg_id: str) -> None:
        current = await self._state.get_leg_state(leg_id)
        if current and current.state == TelephonyLegState.TERMINATED:
            return

        try:
            await self._post("/commands/hangup", {"call_id": leg_id})
        except TelephonyError as exc:
            if "not found" not in str(exc).lower() and "404" not in str(exc):
                raise
        await self._state.set_leg_state(leg_id, TelephonyLegState.TERMINATED)

    async def get_leg_state(self, leg_id: str) -> TelephonyLegState:
        snap = await self._state.get_leg_state(leg_id)
        if snap is not None:
            return snap.state

        corr_state = await self._corr.get_effective_state(leg_id)
        if corr_state is not None:
            await self._state.set_leg_state(leg_id, corr_state)
            return corr_state

        mapped = await self._poll_leg_state(leg_id)
        if mapped is None:
            return TelephonyLegState.FAILED
        await self._state.set_leg_state(leg_id, mapped)
        return mapped

    async def _poll_answer_state_with_fallback(self, leg_id: str) -> Optional[TelephonyLegState]:
        corr_state = await self._corr.get_effective_state(leg_id)
        if corr_state in {
            TelephonyLegState.ANSWERED,
            TelephonyLegState.BRIDGED,
            TelephonyLegState.TERMINATED,
            TelephonyLegState.FAILED,
        }:
            return corr_state
        return await self._poll_leg_state(leg_id)

    async def _wait_for_bridge_confirmation(self, customer_leg_id: str, manager_leg_id: str) -> None:
        bridge_key = MangoEventProcessor.bridge_key(customer_leg_id, manager_leg_id)
        wait_timeout = float(settings.mango_bridge_confirm_timeout_seconds)
        status = await self._state.wait_for_bridge_status(
            bridge_key=bridge_key,
            accepted={"bridge_confirmed"},
            failed={"bridge_failed"},
            timeout=wait_timeout,
        )
        if status == "bridge_confirmed":
            return

        # Poll fallback for environments where provider does not emit explicit bridge webhook.
        customer = await self.get_leg_state(customer_leg_id)
        manager = await self.get_leg_state(manager_leg_id)
        if customer == TelephonyLegState.BRIDGED and manager == TelephonyLegState.BRIDGED:
            await self._state.set_bridge_status(bridge_key, "bridge_confirmed")
            return

        await self._state.set_bridge_status(bridge_key, "bridge_failed")
        raise TelephonyError(
            "Bridge confirmation failed",
            detail={
                "customer_leg_id": customer_leg_id,
                "manager_leg_id": manager_leg_id,
                "bridge_status": status,
                "customer_state": customer.value,
                "manager_state": manager.value,
            },
        )

    def _sign(self, params: dict) -> dict:
        json_str = json.dumps(params, ensure_ascii=False, separators=(",", ":"))
        sign_str = self._api_key + json_str + self._api_salt
        sign = hashlib.sha256(sign_str.encode("utf-8")).hexdigest()
        return {"vpbx_api_key": self._api_key, "sign": sign, "json": json_str}

    async def _post(self, path: str, params: dict) -> dict:
        signed = self._sign(params)
        try:
            resp = await self._http.post(path, data=signed)
        except httpx.RequestError as exc:
            raise TelephonyError(f"Mango network error: {exc}") from exc

        if resp.status_code >= 400:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            raise TelephonyError(f"Mango API error {resp.status_code} on {path}", detail=detail)

        try:
            return resp.json()
        except Exception:
            return {"raw": resp.text}

    async def _poll_leg_state(self, leg_id: str) -> Optional[TelephonyLegState]:
        try:
            # Mango stats uses signed GET params in production. We keep compatibility
            # with existing integration and map common response shapes.
            resp = await self._http.get(f"/stats/request?recording_id={leg_id}")
            if resp.status_code >= 400:
                return None
            data = resp.json()
            raw_state = (data.get("data", [{}])[0] or {}).get("state", "")
            return _MANGO_STATE_MAP.get(str(raw_state), None)
        except Exception as exc:
            log.debug("mango_telephony.poll_state_error", leg_id=leg_id, error=str(exc))
            return None


_MANGO_STATE_MAP: dict[str, TelephonyLegState] = {
    "0": TelephonyLegState.INITIATING,
    "1": TelephonyLegState.RINGING,
    "2": TelephonyLegState.ANSWERED,
    "3": TelephonyLegState.BRIDGED,
    "4": TelephonyLegState.TERMINATED,
    "5": TelephonyLegState.FAILED,
    "Initiating": TelephonyLegState.INITIATING,
    "Ringing": TelephonyLegState.RINGING,
    "Connected": TelephonyLegState.ANSWERED,
    "OnHold": TelephonyLegState.BRIDGED,
    "Disconnected": TelephonyLegState.TERMINATED,
    "Busy": TelephonyLegState.FAILED,
    "Unavailable": TelephonyLegState.FAILED,
}

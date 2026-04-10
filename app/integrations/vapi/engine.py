"""
VapiCallEngine — production call engine using the Vapi API.
Implements AbstractCallEngine by delegating to VapiClient for HTTP calls.
Status updates happen asynchronously via webhooks (see event_processor.py),
not via polling from this class.
"""
from __future__ import annotations
from app.core.config import settings as app_settings
from app.core.logging import get_logger
from app.integrations.call_engine.base import AbstractCallEngine, EngineCallResult
from app.integrations.vapi.client import VapiClient
from app.models.call import Call, CallStatus
log = get_logger(__name__)
# Vapi status string → our internal CallStatus
_VAPI_STATUS_MAP: dict[str, CallStatus] = {
    "queued": CallStatus.QUEUED,
    "ringing": CallStatus.RINGING,
    "in-progress": CallStatus.IN_PROGRESS,
    "forwarding": CallStatus.TRANSFERRING,
    "ended": CallStatus.COMPLETED,
}
class VapiCallEngine(AbstractCallEngine):
    """
    Real call engine backed by the Vapi API.
    One instance is shared per request (created in deps.py).
    The underlying VapiClient manages an httpx connection pool.
    """
    def __init__(self) -> None:
        self._client = VapiClient(app_settings)
    async def initiate_call(self, call: Call) -> EngineCallResult:
        """
        Create an outbound phone call via Vapi.
        Passes our internal call_id in metadata so Vapi webhooks can be
        correlated back to the correct Call record without hitting the DB
        on every event (via vapi_call_id lookup).
        """
        log.info("vapi_engine.initiate_call", call_id=str(call.id), phone=call.phone)
        vapi_resp = await self._client.create_phone_call(
            customer_phone=call.phone,
            metadata={"internal_call_id": str(call.id)},
        )
        vapi_call_id = vapi_resp.get("id")
        vapi_status_raw = vapi_resp.get("status", "queued")
        initial_status = _VAPI_STATUS_MAP.get(vapi_status_raw, CallStatus.QUEUED)
        log.info(
            "vapi_engine.call_created",
            call_id=str(call.id),
            vapi_call_id=vapi_call_id,
            vapi_status=vapi_status_raw,
        )
        # Vapi uses its own call ID as the telephony leg identifier
        # SIP Call-ID is embedded in Vapi call object if available
        sip_call_id = vapi_resp.get("sip", {}).get("callId") if isinstance(vapi_resp.get("sip"), dict) else None

        return EngineCallResult(
            external_id=vapi_call_id,
            initial_status=initial_status,
            route_used="vapi",
            telephony_leg_id=sip_call_id or vapi_call_id,
            provider_metadata={"vapi_status": vapi_status_raw, "vapi_id": vapi_call_id},
            metadata={"vapi_raw": vapi_resp},
        )
    async def stop_call(self, call: Call) -> None:
        """Terminate the call via Vapi DELETE /call/{id}."""
        if not call.vapi_call_id:
            log.warning(
                "vapi_engine.stop_call.no_vapi_id", call_id=str(call.id)
            )
            return
        log.info("vapi_engine.stop_call", vapi_call_id=call.vapi_call_id)
        await self._client.delete_call(call.vapi_call_id)
    async def send_instruction(self, call: Call, instruction: str) -> None:
        """
        Inject a steering instruction into the live call.
        Delivered as a system message to the AI via Vapi's inject-message API.
        Best-effort: errors are logged but do not propagate.
        """
        if not call.vapi_call_id:
            log.warning(
                "vapi_engine.send_instruction.no_vapi_id", call_id=str(call.id)
            )
            return
        await self._client.inject_message(call.vapi_call_id, instruction)
    async def get_status(self, call: Call) -> CallStatus:
        """
        Poll Vapi for the current call status.
        Used for reconciliation if a webhook was missed.
        """
        if not call.vapi_call_id:
            return call.status
        vapi_data = await self._client.get_call(call.vapi_call_id)
        raw_status = vapi_data.get("status", "")
        return _VAPI_STATUS_MAP.get(raw_status, call.status)
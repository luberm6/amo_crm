from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_call_engine, _maybe_inject_session_factory
from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.telephony.mango_events import MangoNormalizedEvent
from app.integrations.telephony.base import TelephonyLegState
from app.models.call import Call, CallMode
from app.repositories.call_repo import CallRepository
from app.services.call_service import CallService
from app.services.telephony_routing_service import InboundRoutingResult

log = get_logger(__name__)


@dataclass(frozen=True)
class MangoInboundLaunchResult:
    status: str
    reason: Optional[str] = None
    call: Optional[Call] = None


class MangoInboundCallService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.call_repo = CallRepository(Call, session)

    async def ensure_inbound_call(
        self,
        *,
        event: MangoNormalizedEvent,
        routing: InboundRoutingResult,
    ) -> MangoInboundLaunchResult:
        if event.leg_id is None:
            return MangoInboundLaunchResult(status="skipped", reason="missing_leg_id")
        if routing.agent is None or routing.telephony_line is None:
            return MangoInboundLaunchResult(status="skipped", reason="agent_not_resolved")
        if event.state in {TelephonyLegState.TERMINATED, TelephonyLegState.FAILED}:
            return MangoInboundLaunchResult(status="skipped", reason="terminal_event")

        existing = await self.call_repo.get_by_telephony_leg_id(event.leg_id)
        if existing is not None:
            return MangoInboundLaunchResult(status="existing_call", call=existing)

        readiness_errors: list[str] = []
        if not settings.gemini_configured:
            readiness_errors.append("gemini_not_configured")
        if not settings.media_gateway_enabled:
            readiness_errors.append("media_gateway_disabled")
        if settings.media_gateway_provider != "freeswitch":
            readiness_errors.append("media_gateway_not_freeswitch")
        if settings.media_gateway_mode not in {"mock", "esl_rtp"}:
            readiness_errors.append("media_gateway_mode_not_supported")
        if readiness_errors:
            return MangoInboundLaunchResult(
                status="blocked",
                reason=",".join(readiness_errors),
            )

        engine = await get_call_engine()
        _maybe_inject_session_factory(engine, self.session)
        call_service = CallService(session=self.session, engine=engine)
        caller_phone = event.from_number or event.to_number or "mango:inbound"
        runtime_context = {
            "telephony": {
                "existing_leg_id": event.leg_id,
                "mango_leg_id": event.leg_id,
                "telephony_provider": "mango",
                "telephony_line_id": str(routing.telephony_line.id),
                "telephony_remote_line_id": routing.telephony_line.remote_line_id,
                "telephony_line_phone_number": routing.telephony_line.phone_number,
                "telephony_line_label": routing.telephony_line.label,
                "telephony_extension": routing.agent.telephony_extension or routing.telephony_line.extension,
                "call_id": None,
                "inbound": True,
            }
        }
        call = await call_service.create_call(
            raw_phone=caller_phone,
            mode=CallMode.DIRECT,
            actor="mango_webhook",
            agent_profile_id=routing.agent.id,
            runtime_context=runtime_context,
            skip_policy_checks=True,
        )
        log.info(
            "mango_inbound.call_started",
            call_id=str(call.id),
            telephony_leg_id=call.telephony_leg_id,
            agent_id=str(routing.agent.id),
            agent_name=routing.agent.name,
            remote_line_id=routing.telephony_line.remote_line_id,
        )
        return MangoInboundLaunchResult(status="started", call=call)

    async def ensure_inbound_sip_call(
        self,
        *,
        provider: str,
        call_uuid: str,
        to_number: str,
        from_number: Optional[str],
        routing: InboundRoutingResult,
    ) -> MangoInboundLaunchResult:
        if not call_uuid:
            return MangoInboundLaunchResult(status="skipped", reason="missing_call_uuid")
        if routing.agent is None or routing.telephony_line is None:
            return MangoInboundLaunchResult(status="skipped", reason="agent_not_resolved")

        existing = await self.call_repo.get_by_telephony_leg_id(call_uuid)
        if existing is not None:
            return MangoInboundLaunchResult(status="existing_call", call=existing)

        readiness_errors: list[str] = []
        if not settings.gemini_configured:
            readiness_errors.append("gemini_not_configured")
        if not settings.media_gateway_enabled:
            readiness_errors.append("media_gateway_disabled")
        if settings.media_gateway_provider != "freeswitch":
            readiness_errors.append("media_gateway_not_freeswitch")
        if settings.media_gateway_mode not in {"mock", "esl_rtp"}:
            readiness_errors.append("media_gateway_mode_not_supported")
        if readiness_errors:
            return MangoInboundLaunchResult(
                status="blocked",
                reason=",".join(readiness_errors),
            )

        engine = await get_call_engine()
        _maybe_inject_session_factory(engine, self.session)
        call_service = CallService(session=self.session, engine=engine)
        caller_phone = from_number or to_number or "sip:inbound"
        runtime_context = {
            "telephony": {
                "existing_leg_id": call_uuid,
                "provider_leg_id": call_uuid,
                "mango_leg_id": call_uuid,
                "freeswitch_uuid": call_uuid,
                "telephony_provider": provider,
                "telephony_line_id": str(routing.telephony_line.id),
                "telephony_remote_line_id": routing.telephony_line.remote_line_id,
                "telephony_line_phone_number": routing.telephony_line.phone_number,
                "telephony_line_label": routing.telephony_line.label,
                "telephony_extension": routing.agent.telephony_extension or routing.telephony_line.extension,
                "call_id": None,
                "inbound": True,
            }
        }
        call = await call_service.create_call(
            raw_phone=caller_phone,
            mode=CallMode.DIRECT,
            actor="freeswitch_inbound_sip",
            agent_profile_id=routing.agent.id,
            runtime_context=runtime_context,
            skip_policy_checks=True,
        )
        if not call.telephony_leg_id:
            call.telephony_leg_id = call_uuid
            await self.call_repo.save(call)
        log.info(
            "freeswitch_inbound.call_started",
            call_id=str(call.id),
            telephony_leg_id=call.telephony_leg_id,
            agent_id=str(routing.agent.id),
            agent_name=routing.agent.name,
            remote_line_id=routing.telephony_line.remote_line_id,
            freeswitch_uuid=call_uuid,
        )
        return MangoInboundLaunchResult(status="started", call=call)

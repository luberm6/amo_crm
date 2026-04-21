from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.integrations.telephony.mango_events import (
    MangoEventProcessor,
    verify_mango_webhook_guard,
)
from app.integrations.telephony.mango_state_store import (
    InMemoryMangoLegStateStore,
    RedisMangoLegStateStore,
)
from app.core.logging import get_logger
from app.core.redis_client import get_redis
from app.schemas.telephony import MangoWebhookReceipt, MangoWebhookRoutingSummary
from app.schemas.telephony import MangoInboundLaunchSummary
from app.schemas.telephony import FreeSwitchInboundSipReceipt, FreeSwitchInboundSipRequest
from app.services.mango_inbound_call_service import MangoInboundCallService
from app.services.telephony_routing_service import TelephonyRoutingService

log = get_logger(__name__)
router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])

_in_memory_store = InMemoryMangoLegStateStore()


def _get_state_store():
    redis = get_redis()
    if redis is not None:
        return RedisMangoLegStateStore(redis)
    return _in_memory_store


def _verify_provider_secret(header_value: Optional[str]) -> bool:
    expected = (settings.provider_settings_secret or settings.admin_auth_secret or "").strip()
    provided = (header_value or "").strip()
    return bool(expected and provided and expected == provided)


@router.post("/mango", status_code=status.HTTP_200_OK, response_model=MangoWebhookReceipt)
async def mango_webhook(
    request: Request,
    session: AsyncSession = Depends(get_db),
    x_mango_signature: Optional[str] = Header(default=None, alias="x-mango-signature"),
    x_mango_webhook_secret: Optional[str] = Header(default=None, alias="x-mango-webhook-secret"),
) -> JSONResponse:
    request_id = str(uuid.uuid4())
    source_ip = request.client.host if request.client else None

    raw_body = await request.body()

    # ── Parse payload ─────────────────────────────────────────────────────────
    try:
        payload = await request.json()
    except Exception:
        log.warning(
            "mango_webhook.bad_payload",
            request_id=request_id,
            source_ip=source_ip,
            error="invalid_json",
        )
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "mango_webhook_bad_payload", "detail": "Body is not valid JSON"},
        )

    provider_type = payload.get("event") or payload.get("event_type") or payload.get("type") or "unknown"
    log.info(
        "mango_webhook.received",
        request_id=request_id,
        source_ip=source_ip,
        provider_type=provider_type,
        payload_keys=sorted(payload.keys()),
    )

    # ── Security warning when no secrets configured ───────────────────────────
    webhook_secured = bool(
        settings.mango_webhook_secret
        or settings.mango_webhook_shared_secret
        or settings.mango_webhook_ip_allowlist.strip()
    )
    if not webhook_secured:
        log.warning(
            "mango_webhook.not_configured",
            request_id=request_id,
            detail="No MANGO_WEBHOOK_SECRET, MANGO_WEBHOOK_SHARED_SECRET, or IP allowlist configured. "
                   "Accepting request without verification — set a secret before production use.",
        )

    # ── Signature / secret / IP guard ────────────────────────────────────────
    ok, reason = verify_mango_webhook_guard(
        raw_body=raw_body,
        source_ip=source_ip,
        signature_header=x_mango_signature,
        secret_header=x_mango_webhook_secret,
    )
    if not ok:
        log.warning(
            "mango_webhook.signature_invalid",
            request_id=request_id,
            source_ip=source_ip,
            reason=reason,
        )
        error_code = "mango_webhook_invalid_signature"
        if reason in ("missing_signature", "missing_shared_secret"):
            error_code = "mango_webhook_not_configured"
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"error": error_code, "detail": reason},
        )

    log.info(
        "mango_webhook.signature_valid",
        request_id=request_id,
        secured=webhook_secured,
    )

    # ── Process event ─────────────────────────────────────────────────────────
    processor = MangoEventProcessor(session=session, store=_get_state_store())
    event = await processor.process(payload)

    log.info(
        "mango_webhook.payload_parsed",
        request_id=request_id,
        event_id=event.provider_event_id,
        provider_type=event.provider_type,
        leg_id=event.leg_id,
        command_id=event.command_id,
        state=event.state.value if event.state else None,
        from_number=event.from_number,
        to_number=event.to_number,
        call_id=event.call_id,
        transfer_id=event.transfer_id,
    )

    # ── Inbound routing (informational — log which agent would handle) ────────
    routing_summary: Optional[MangoWebhookRoutingSummary] = None
    inbound_launch_summary: Optional[MangoInboundLaunchSummary] = None
    if event.to_number:
        routing_svc = TelephonyRoutingService(session)
        result = await routing_svc.resolve_inbound(
            provider="mango",
            phone_number=event.to_number,
        )
        routing_summary = MangoWebhookRoutingSummary(
            phone_number_input=event.to_number,
            phone_number_normalized=result.phone_number_normalized,
            line_found=result.telephony_line is not None,
            line_id=result.telephony_line.id if result.telephony_line else None,
            remote_line_id=result.telephony_line.remote_line_id if result.telephony_line else None,
            line_phone_number=result.telephony_line.phone_number if result.telephony_line else None,
            line_schema_name=result.telephony_line.schema_name if result.telephony_line else None,
            line_label=result.telephony_line.label if result.telephony_line else None,
            agent_found=result.agent is not None,
            agent_id=result.agent.id if result.agent else None,
            agent_name=result.agent.name if result.agent else None,
            ambiguous=result.ambiguous,
            candidate_count=result.candidate_count,
        )
        if result.agent is None:
            log.info(
                "mango_inbound.agent_not_found",
                request_id=request_id,
                to_number=event.to_number,
                phone_number_normalized=result.phone_number_normalized,
                event_id=event.provider_event_id,
            )
        else:
            log.info(
                "mango_inbound.agent_resolved",
                request_id=request_id,
                to_number=event.to_number,
                phone_number_normalized=result.phone_number_normalized,
                agent_id=str(result.agent.id),
                agent_name=result.agent.name,
                ambiguous=result.ambiguous,
                event_id=event.provider_event_id,
            )
            inbound_service = MangoInboundCallService(session)
            launch = await inbound_service.ensure_inbound_call(event=event, routing=result)
            inbound_launch_summary = MangoInboundLaunchSummary(
                status=launch.status,
                reason=launch.reason,
                call_id=launch.call.id if launch.call else None,
                telephony_leg_id=launch.call.telephony_leg_id if launch.call else event.leg_id,
            )
            log.info(
                "mango_inbound.call_launch_result",
                request_id=request_id,
                event_id=event.provider_event_id,
                status=launch.status,
                reason=launch.reason,
                call_id=str(launch.call.id) if launch.call else None,
            )

    return JSONResponse(
        content={
            "status": "ok",
            "event_id": event.provider_event_id,
            "event_type": event.provider_type,
            "webhook_secured": webhook_secured,
            "routing": routing_summary.model_dump(mode="json") if routing_summary else None,
            "inbound_launch": inbound_launch_summary.model_dump(mode="json") if inbound_launch_summary else None,
        }
    )


@router.post("/freeswitch/inbound-sip", status_code=status.HTTP_200_OK, response_model=FreeSwitchInboundSipReceipt)
async def freeswitch_inbound_sip(
    body: FreeSwitchInboundSipRequest,
    session: AsyncSession = Depends(get_db),
    x_provider_settings_secret: Optional[str] = Header(default=None, alias="x-provider-settings-secret"),
) -> JSONResponse:
    request_id = str(uuid.uuid4())
    if not _verify_provider_secret(x_provider_settings_secret):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "accepted": False,
                "status": "unauthorized",
                "provider": body.provider,
                "call_uuid": body.call_uuid,
                "to_number": body.to_number,
                "from_number": body.from_number,
                "agent_found": False,
                "error": "invalid_provider_settings_secret",
            },
        )

    routing_svc = TelephonyRoutingService(session)
    route_number = body.line_phone_number or body.to_number
    result = await routing_svc.resolve_inbound(
        provider=body.provider,
        phone_number=route_number,
    )
    inbound_service = MangoInboundCallService(session)
    launch = await inbound_service.ensure_inbound_sip_call(
        provider=body.provider,
        call_uuid=body.call_uuid,
        to_number=body.to_number,
        from_number=body.from_number,
        routing=result,
    )
    log.info(
        "freeswitch_inbound.call_launch_result",
        request_id=request_id,
        status=launch.status,
        reason=launch.reason,
        call_id=str(launch.call.id) if launch.call else None,
        freeswitch_uuid=body.call_uuid,
        to_number=body.to_number,
    )
    return JSONResponse(
        content=FreeSwitchInboundSipReceipt(
            accepted=launch.status in {"started", "existing_call"},
            status=launch.status,
            provider=body.provider,
            call_uuid=body.call_uuid,
            to_number=body.to_number,
            from_number=body.from_number,
            agent_found=result.agent is not None,
            agent_id=result.agent.id if result.agent else None,
            agent_name=result.agent.name if result.agent else None,
            call_id=launch.call.id if launch.call else None,
            telephony_leg_id=launch.call.telephony_leg_id if launch.call else body.call_uuid,
            error=launch.reason,
        ).model_dump(mode="json")
    )

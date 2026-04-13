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
from app.services.telephony_routing_service import TelephonyRoutingService

log = get_logger(__name__)
router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])

_in_memory_store = InMemoryMangoLegStateStore()


def _get_state_store():
    redis = get_redis()
    if redis is not None:
        return RedisMangoLegStateStore(redis)
    return _in_memory_store


@router.post("/mango", status_code=status.HTTP_200_OK)
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
        state=event.state.value if event.state else None,
        from_number=event.from_number,
        to_number=event.to_number,
        call_id=event.call_id,
        transfer_id=event.transfer_id,
    )

    # ── Inbound routing (informational — log which agent would handle) ────────
    if event.to_number:
        routing_svc = TelephonyRoutingService(session)
        result = await routing_svc.resolve_inbound(
            provider="mango",
            phone_number=event.to_number,
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

    return JSONResponse(
        content={
            "status": "ok",
            "event_id": event.provider_event_id,
            "event_type": event.provider_type,
        }
    )

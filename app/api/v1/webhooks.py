"""
Vapi webhook endpoint.
Receives all server events from Vapi and routes them to VapiEventProcessor.
Security:
- If VAPI_WEBHOOK_SECRET is set, validates HMAC-SHA256 signature
- Always returns HTTP 200 to Vapi (Vapi retries on non-200)
- Processing errors are logged but don't change the response code
Vapi retry behavior: Vapi retries failed (non-200) webhook deliveries up to
5 times with exponential backoff. We always return 200 and handle failures
internally to avoid spurious retries.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import get_db
from app.integrations.vapi.event_processor import (
    VapiEventProcessor,
    verify_webhook_signature,
)
log = get_logger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])
@router.post("/vapi", status_code=status.HTTP_200_OK)
async def vapi_webhook(
    request: Request,
    session: AsyncSession = Depends(get_db),
    x_vapi_signature: Optional[str] = Header(default=None, alias="x-vapi-signature"),
) -> JSONResponse:
    """
    Receive and process Vapi server events.
    Returns 200 immediately after persisting the raw event.
    Processing errors are stored in VapiEventLog and visible in logs.
    """
    raw_body = await request.body()
    # ── Signature verification ─────────────────────────────────────────────
    if settings.vapi_webhook_secret:
        if not x_vapi_signature:
            # Secret is configured but no signature provided — reject.
            # If Vapi is not sending signatures, ensure VAPI_WEBHOOK_SECRET matches
            # the value in Vapi assistant → Server → Secret.
            log.warning("vapi_webhook.missing_signature")
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"error": "missing_signature"},
            )
        elif not verify_webhook_signature(
            raw_body, x_vapi_signature, settings.vapi_webhook_secret
        ):
            log.warning("vapi_webhook.invalid_signature")
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"error": "invalid_signature"},
            )
    # ── Parse payload ──────────────────────────────────────────────────────
    try:
        payload = await request.json()
    except Exception as exc:
        log.warning("vapi_webhook.invalid_json", error=str(exc))
        return JSONResponse(content={"status": "ok"})  # Don't let Vapi retry bad JSON
    event_type = payload.get("message", {}).get("type", "unknown")
    log.info("vapi_webhook.received", event_type=event_type)
    # ── Process ────────────────────────────────────────────────────────────
    processor = VapiEventProcessor(session)
    await processor.process(payload)
    return JSONResponse(content={"status": "ok"})
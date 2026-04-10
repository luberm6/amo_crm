from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

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
    raw_body = await request.body()
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"error": "invalid_json"})

    source_ip = request.client.host if request.client else None
    ok, reason = verify_mango_webhook_guard(
        raw_body=raw_body,
        source_ip=source_ip,
        signature_header=x_mango_signature,
        secret_header=x_mango_webhook_secret,
    )
    if not ok:
        log.warning("mango_webhook.rejected", reason=reason, source_ip=source_ip)
        return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"error": reason})

    processor = MangoEventProcessor(session=session, store=_get_state_store())
    event = await processor.process(payload)
    return JSONResponse(
        content={
            "status": "ok",
            "event_id": event.provider_event_id,
            "event_type": event.provider_type,
        }
    )

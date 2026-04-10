"""
Health endpoints.
GET /health  — liveness probe (is the process alive?)
GET /ready   — readiness probe (can we serve traffic? DB + Redis reachable?)
"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends
from app.api.auth import require_api_key
from app.db.session import get_db
import redis.asyncio as aioredis
from app.core.config import settings
from app.core.telemetry import render_metrics
from app.services.preflight_service import DirectVoicePreflightService
router = APIRouter(tags=["health"])
@router.get("/health")
async def health() -> dict:
    """Liveness check — always returns OK if the process is running."""
    return {"status": "ok"}
@router.get("/ready")
async def ready(session: AsyncSession = Depends(get_db)) -> dict:
    """
    Readiness check — verifies DB and Redis connectivity.
    Returns 200 only when all dependencies are reachable.
    Kubernetes / Render will stop sending traffic if this returns non-200.
    """
    checks: dict[str, str] = {}
    # DB check
    try:
        await session.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"error: {exc}"
    # Redis check
    try:
        client = aioredis.from_url(settings.redis_url, socket_connect_timeout=2)
        await client.ping()
        await client.aclose()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {exc}"
    all_ok = all(v == "ok" for v in checks.values())
    return {"status": "ok" if all_ok else "degraded", **checks}


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """
    Prometheus scrape endpoint for runtime voice/media telemetry.
    """
    if not settings.metrics_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="metrics_disabled",
        )
    payload, content_type = render_metrics()
    return Response(content=payload, media_type=content_type)


@router.get("/v1/preflight/direct-voice", dependencies=[Depends(require_api_key)])
async def direct_voice_preflight(session: AsyncSession = Depends(get_db)) -> dict:
    """
    Validate the Direct voice contour without placing a real call.
    """
    service = DirectVoicePreflightService(session)
    return await service.run()

"""
Rate limiting middleware for ASGI.
Enforces per-IP global request rate limit at the middleware layer (outermost).
Fail-open: if Redis is unavailable, requests are allowed to proceed.
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.core.exceptions import RateLimitError
from app.core.logging import get_logger
from app.core.rate_limit import RateLimiter
from app.core.redis_client import get_redis

log = get_logger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Global per-IP request rate limiting.
    Runs before all endpoints to catch request floods early.

    Returns 429 if IP exceeds global per-minute limit.
    Returns 200+ response from endpoint otherwise.
    """

    async def dispatch(self, request: Request, call_next):
        if not settings.rate_limit_enabled:
            return await call_next(request)

        # Extract client IP
        ip = request.client.host if request.client else "unknown"

        # Check global per-IP per-minute limit
        limiter = RateLimiter(get_redis())
        try:
            await limiter.check_fixed_window(
                key=f"rl:global:ip:{ip}",
                limit=settings.rate_limit_global_per_ip_per_minute,
                window_seconds=60,
                label=f"global requests per IP ({ip})",
            )
        except RateLimitError as exc:
            log.warning("middleware_rate_limit_exceeded", ip=ip)
            return JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limit_exceeded",
                    "message": "Too many requests",
                    "detail": exc.detail,
                },
            )

        # Proceed to endpoint
        return await call_next(request)

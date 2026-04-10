"""
Security headers middleware.

Adds a minimal set of defensive HTTP headers to every response.
These protect against common browser-based attacks (XSS, clickjacking, MIME
sniffing). They are cheap to add and expected by security scanners.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add defensive security headers to all responses."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        response: Response = await call_next(request)
        response.headers.update(
            {
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "X-XSS-Protection": "1; mode=block",
                "Referrer-Policy": "strict-origin-when-cross-origin",
                # CSP: API-only backend — no scripts, no styles needed
                "Content-Security-Policy": "default-src 'none'",
                # Strict Transport Security (only relevant behind HTTPS terminator)
                "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
            }
        )
        return response

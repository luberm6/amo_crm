"""
Request ID middleware.

Assigns a unique X-Request-ID to every request:
- If the client sends X-Request-ID, that value is used (correlation).
- Otherwise a new UUID4 is generated.

The ID is:
- Bound to the structlog context so every log line in the request carries it.
- Echoed in the X-Request-ID response header.

Usage in logs:
    log.info("call_created", ...)   # automatically includes request_id=<uuid>
"""
from __future__ import annotations

import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach a unique request ID to every request and response."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        request_id = (
            request.headers.get("x-request-id")
            or request.headers.get("X-Request-ID")
            or str(uuid.uuid4())
        )

        # Bind to structlog context — cleared automatically per-request by
        # structlog.contextvars (each request gets a fresh context dict).
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

"""
CORS middleware for public widget endpoints.

Adds Access-Control-Allow-Origin: * to:
  - /public/* — widget API endpoints called from third-party websites
  - /widget.js — the embeddable script itself

The existing admin CORS middleware (CORSMiddleware) is NOT replaced — this
middleware only targets public paths and leaves admin paths unchanged.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

_PUBLIC_PREFIXES = ("/public/", "/widget.js", "/widget_test.html")


def _is_public_path(path: str) -> bool:
    return any(path == p or path.startswith(p) for p in _PUBLIC_PREFIXES)


class PublicCORSMiddleware(BaseHTTPMiddleware):
    """Allow cross-origin requests to public widget endpoints from any domain."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        if not _is_public_path(request.url.path):
            return await call_next(request)

        if request.method == "OPTIONS":
            return Response(
                status_code=204,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                    "Access-Control-Allow-Headers": "Content-Type",
                    "Access-Control-Max-Age": "86400",
                },
            )

        response: Response = await call_next(request)
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response

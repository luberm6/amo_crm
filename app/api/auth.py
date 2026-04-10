"""
API key authentication.

When settings.api_key is non-empty, all mutating endpoints require the caller
to present a matching X-API-Key header.  Read-only endpoints (GET /health,
GET /ready, GET /v1/calls/*) are intentionally exempt.

Usage:
    from app.api.auth import require_api_key
    @router.post("", dependencies=[Depends(require_api_key)])
    async def create_call(...): ...

Or add at router level:
    router = APIRouter(dependencies=[Depends(require_api_key)])
"""
from __future__ import annotations

import hmac
from typing import Optional

from fastapi import Depends, Header, HTTPException, status

from app.core.config import settings


async def require_api_key(
    x_api_key: Optional[str] = Header(default=None, alias="x-api-key"),
) -> None:
    """
    FastAPI dependency: validate X-API-Key header.

    - If settings.api_key is empty → auth is disabled, all requests pass.
    - If settings.api_key is set → header must match (constant-time compare).
    """
    if not settings.api_key:
        # Auth not configured — open access (development mode)
        return

    if x_api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "missing_api_key", "message": "X-API-Key header is required"},
        )

    # Use hmac.compare_digest to prevent timing attacks
    if not hmac.compare_digest(x_api_key, settings.api_key):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "invalid_api_key", "message": "Invalid API key"},
        )

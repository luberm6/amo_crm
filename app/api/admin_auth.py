from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Optional

from fastapi import Header, HTTPException, status

from app.core.config import settings


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))


def _sign(payload_b64: str) -> str:
    digest = hmac.new(
        settings.admin_auth_secret.encode("utf-8"),
        payload_b64.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return _b64url_encode(digest)


def create_admin_token(email: str) -> str:
    if not settings.admin_auth_configured:
        raise RuntimeError("Admin auth is not configured")

    now = int(time.time())
    payload = {
        "sub": email,
        "role": "admin",
        "iat": now,
        "exp": now + int(settings.admin_token_ttl_seconds),
    }
    payload_b64 = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    return f"{payload_b64}.{_sign(payload_b64)}"


def decode_admin_token(token: str) -> dict[str, Any]:
    if not settings.admin_auth_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "admin_auth_not_configured",
                "message": "Admin auth is not configured on the backend.",
            },
        )

    try:
        payload_b64, signature = token.split(".", 1)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_admin_token", "message": "Malformed admin token."},
        ) from exc

    expected_signature = _sign(payload_b64)
    if not hmac.compare_digest(signature, expected_signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_admin_token", "message": "Invalid admin token signature."},
        )

    try:
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_admin_token", "message": "Unreadable admin token payload."},
        ) from exc

    exp = int(payload.get("exp", 0))
    if exp <= int(time.time()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "expired_admin_token", "message": "Admin token has expired."},
        )

    if payload.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "invalid_admin_role", "message": "Admin token role is invalid."},
        )

    return payload


async def require_admin_auth(
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    if not settings.admin_auth_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "admin_auth_not_configured",
                "message": "Admin auth is not configured on the backend.",
            },
        )

    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "missing_admin_token",
                "message": "Authorization: Bearer <token> header is required.",
            },
        )

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "invalid_admin_token",
                "message": "Authorization header must use Bearer token format.",
            },
        )

    return decode_admin_token(token)

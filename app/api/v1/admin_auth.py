from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.admin_auth import create_admin_token, require_admin_auth
from app.core.config import settings
from app.schemas.admin_auth import AdminLoginRequest, AdminLoginResponse, AdminUserRead

router = APIRouter(prefix="/admin/auth", tags=["admin-auth"])


@router.post("/login", response_model=AdminLoginResponse)
async def admin_login(body: AdminLoginRequest) -> AdminLoginResponse:
    if not settings.admin_auth_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "admin_auth_not_configured",
                "message": "Admin auth is not configured on the backend.",
            },
        )

    email_matches = hmac.compare_digest(body.email.strip().lower(), settings.admin_email.strip().lower())
    password_matches = hmac.compare_digest(body.password, settings.admin_password)
    if not email_matches or not password_matches:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "invalid_admin_credentials",
                "message": "Invalid admin email or password.",
            },
        )

    token = create_admin_token(settings.admin_email)
    return AdminLoginResponse.from_token(token=token, email=settings.admin_email)


@router.get("/me", response_model=AdminUserRead)
async def admin_me(payload: dict = Depends(require_admin_auth)) -> AdminUserRead:
    return AdminUserRead(email=str(payload["sub"]))

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.core.config import settings


class AdminLoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=256)


class AdminUserRead(BaseModel):
    email: str
    role: str = "admin"


class AdminLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: AdminUserRead

    @classmethod
    def from_token(cls, token: str, email: str) -> "AdminLoginResponse":
        expires_at = datetime.now(timezone.utc).timestamp() + int(settings.admin_token_ttl_seconds)
        return cls(
            access_token=token,
            expires_at=datetime.fromtimestamp(expires_at, tz=timezone.utc),
            user=AdminUserRead(email=email),
        )

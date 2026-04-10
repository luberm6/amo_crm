from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


SUPPORTED_PROVIDERS = {"mango", "gemini", "elevenlabs", "vapi"}


class ProviderSecretRead(BaseModel):
    is_set: bool
    masked_value: Optional[str] = None


class ProviderSettingRead(BaseModel):
    provider: str
    display_name: str
    is_enabled: bool
    activation_status: Literal["active", "inactive"]
    status: Literal["configured", "invalid", "not_tested"]
    safe_mode_note: str
    config: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, ProviderSecretRead] = Field(default_factory=dict)
    last_validated_at: Optional[datetime] = None
    last_validation_message: Optional[str] = None
    last_validation_remote_checked: bool = False


class ProviderSettingsListRead(BaseModel):
    items: list[ProviderSettingRead]


class ProviderSettingUpdate(BaseModel):
    is_enabled: Optional[bool] = None
    config: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, Optional[str]] = Field(default_factory=dict)


class ProviderValidationRead(BaseModel):
    provider: str
    status: Literal["configured", "invalid", "not_tested"]
    message: str
    remote_checked: bool
    checked_at: datetime


class ProviderPath(BaseModel):
    provider: str

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned not in SUPPORTED_PROVIDERS:
            raise ValueError("provider must be one of: mango, gemini, elevenlabs, vapi")
        return cleaned

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
import uuid

from pydantic import BaseModel, Field, field_validator


TELEPHONY_PROVIDER_OPTIONS = {"mango"}
VOICE_PROVIDER_OPTIONS = {"elevenlabs", "gemini"}


class TelephonyLineRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    provider: str
    provider_resource_id: str
    phone_number: str
    display_name: Optional[str] = None
    extension: Optional[str] = None
    is_active: bool
    is_inbound_enabled: bool
    is_outbound_enabled: bool
    synced_at: Optional[datetime] = None


class TelephonyLineListRead(BaseModel):
    items: list[TelephonyLineRead]
    total: int


class TelephonyLineSyncRead(BaseModel):
    items: list[TelephonyLineRead]
    total: int
    synced_count: int
    deactivated_count: int
    source: str = "mango_api"
    synced_at: datetime


class TelephonyExtensionRead(BaseModel):
    provider_resource_id: str
    extension: str
    display_name: Optional[str] = None
    line_provider_resource_id: Optional[str] = None
    line_phone_number: Optional[str] = None


class TelephonyExtensionListRead(BaseModel):
    items: list[TelephonyExtensionRead]
    total: int
    source: str = "mango_api"


class MangoReadinessRead(BaseModel):
    api_configured: bool
    webhook_secret_configured: bool
    from_ext_configured: bool
    warnings: list[str]


class AgentProfileSettingsRead(BaseModel):
    agent_profile_id: uuid.UUID
    name: str
    is_active: bool
    system_prompt: str
    tone_rules: Optional[str] = None
    business_rules: Optional[str] = None
    sales_objectives: Optional[str] = None
    greeting_text: Optional[str] = None
    transfer_rules: Optional[str] = None
    prohibited_promises: Optional[str] = None
    voice_strategy: str
    voice_provider: str
    telephony_provider: Optional[str] = None
    telephony_line_id: Optional[uuid.UUID] = None
    telephony_extension: Optional[str] = None
    telephony_line: Optional[TelephonyLineRead] = None
    user_settings: dict[str, Any] = Field(default_factory=dict)
    knowledge_document_ids: list[uuid.UUID] = Field(default_factory=list)
    version: int
    created_at: datetime
    updated_at: datetime
    assembled_prompt_preview: str


class AgentProfileSettingsUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    is_active: Optional[bool] = None
    system_prompt: Optional[str] = Field(default=None, min_length=1)
    tone_rules: Optional[str] = None
    business_rules: Optional[str] = None
    sales_objectives: Optional[str] = None
    greeting_text: Optional[str] = None
    transfer_rules: Optional[str] = None
    prohibited_promises: Optional[str] = None
    voice_provider: Optional[str] = None
    telephony_provider: Optional[str] = None
    telephony_line_id: Optional[uuid.UUID] = None
    telephony_extension: Optional[str] = None
    user_settings: Optional[dict[str, Any]] = None
    knowledge_document_ids: Optional[list[uuid.UUID]] = None

    @field_validator(
        "name",
        "system_prompt",
        "tone_rules",
        "business_rules",
        "sales_objectives",
        "greeting_text",
        "transfer_rules",
        "prohibited_promises",
        "telephony_provider",
        "telephony_extension",
        "voice_provider",
    )
    @classmethod
    def strip_optional_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("telephony_provider")
    @classmethod
    def validate_telephony_provider(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if value not in TELEPHONY_PROVIDER_OPTIONS:
            raise ValueError("telephony_provider must be one of: mango")
        return value

    @field_validator("voice_provider")
    @classmethod
    def validate_voice_provider(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if value not in VOICE_PROVIDER_OPTIONS:
            raise ValueError("voice_provider must be one of: elevenlabs, gemini")
        return value

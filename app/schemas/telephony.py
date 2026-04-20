from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
import uuid

from pydantic import BaseModel, Field, field_validator


TELEPHONY_PROVIDER_OPTIONS = {"mango"}
VOICE_PROVIDER_OPTIONS = {"elevenlabs", "gemini"}


class TelephonyLineRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    provider: str
    provider_resource_id: str
    remote_line_id: str
    phone_number: str
    schema_name: Optional[str] = None
    display_name: Optional[str] = None
    label: str
    extension: Optional[str] = None
    is_active: bool
    is_inbound_enabled: bool
    is_outbound_enabled: bool
    synced_at: Optional[datetime] = None
    is_recommended_for_ai: bool = False
    is_protected: bool = False


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
    from_ext_auto_discoverable: bool = False
    telephony_runtime_provider: str
    telephony_runtime_real: bool
    backend_url: str
    webhook_url: str
    webhook_url_public: bool
    inbound_webhook_smoke_ready: bool
    outbound_originate_smoke_ready: bool
    inbound_ai_runtime_ready: bool
    missing_requirements: list[str] = Field(default_factory=list)
    warnings: list[str]
    route_readiness: dict[str, "MangoRouteReadinessScope"] = Field(default_factory=dict)
    render_summary: "MangoRenderReadinessSummary"
    actionable_next_step: "MangoActionableNextStep"


class MangoRouteReadinessScope(BaseModel):
    key: Literal["inbound_webhook", "outbound_originate", "inbound_ai_runtime"]
    ready: bool
    status: Literal["ready", "blocked"]
    summary: str
    blockers: list[str] = Field(default_factory=list)


class MangoRenderReadinessSummary(BaseModel):
    ready_count: int = 0
    blocked_count: int = 0
    overall_status: Literal["ready", "partial", "blocked"]
    operator_summary: str


class MangoActionableNextStep(BaseModel):
    key: str
    title: str
    description: str
    cta_label: str
    scope: Literal["global", "inbound_webhook", "outbound_originate", "inbound_ai_runtime"]


class MangoRoutingMapItem(BaseModel):
    """One Mango line with its bound agent (if any)."""
    line_id: uuid.UUID
    provider_resource_id: str
    remote_line_id: str
    phone_number: str
    schema_name: Optional[str] = None
    display_name: Optional[str] = None
    label: str
    is_active: bool
    is_inbound_enabled: bool
    is_recommended_for_ai: bool = False
    is_protected: bool = False
    agent_id: Optional[uuid.UUID] = None
    agent_name: Optional[str] = None
    agent_is_active: Optional[bool] = None


class MangoRoutingMapRead(BaseModel):
    items: list[MangoRoutingMapItem]
    total: int


class MangoResolveInboundRequest(BaseModel):
    phone_number: str = Field(..., description="The incoming phone number to resolve (e.g. '+79300350609' or '79300350609')")


class MangoResolveInboundResult(BaseModel):
    phone_number_input: str
    phone_number_normalized: str
    line_found: bool
    line_id: Optional[uuid.UUID] = None
    remote_line_id: Optional[str] = None
    line_phone_number: Optional[str] = None
    line_schema_name: Optional[str] = None
    line_display_name: Optional[str] = None
    line_label: Optional[str] = None
    agent_found: bool
    agent_id: Optional[uuid.UUID] = None
    agent_name: Optional[str] = None
    ambiguous: bool = False
    candidate_count: int = 0


class MangoResolveOutboundResult(BaseModel):
    agent_id: uuid.UUID
    agent_found: bool
    agent_name: Optional[str] = None
    agent_is_active: Optional[bool] = None
    telephony_provider: Optional[str] = None
    line_found: bool
    line_id: Optional[uuid.UUID] = None
    remote_line_id: Optional[str] = None
    line_phone_number: Optional[str] = None
    line_schema_name: Optional[str] = None
    line_display_name: Optional[str] = None
    line_label: Optional[str] = None
    line_is_active: Optional[bool] = None
    from_ext_configured: bool
    resolved_from_ext: Optional[str] = None
    from_ext_source: Optional[str] = None
    originate_ready: bool
    missing_requirements: list[str] = Field(default_factory=list)


class MangoWebhookRoutingSummary(BaseModel):
    phone_number_input: Optional[str] = None
    phone_number_normalized: Optional[str] = None
    line_found: bool = False
    line_id: Optional[uuid.UUID] = None
    remote_line_id: Optional[str] = None
    line_phone_number: Optional[str] = None
    line_schema_name: Optional[str] = None
    line_label: Optional[str] = None
    agent_found: bool = False
    agent_id: Optional[uuid.UUID] = None
    agent_name: Optional[str] = None
    ambiguous: bool = False
    candidate_count: int = 0


class MangoInboundLaunchSummary(BaseModel):
    status: str
    reason: Optional[str] = None
    call_id: Optional[uuid.UUID] = None
    telephony_leg_id: Optional[str] = None


class MangoWebhookReceipt(BaseModel):
    status: str
    event_id: str
    event_type: str
    webhook_secured: bool
    routing: Optional[MangoWebhookRoutingSummary] = None
    inbound_launch: Optional[MangoInboundLaunchSummary] = None


class FreeSwitchInboundSipRequest(BaseModel):
    call_uuid: str = Field(..., min_length=1, max_length=200)
    to_number: str = Field(..., min_length=3, max_length=64)
    from_number: Optional[str] = Field(default=None, max_length=64)
    provider: str = Field(default="mango", min_length=1, max_length=32)
    line_phone_number: Optional[str] = Field(default=None, max_length=64)

    @field_validator("call_uuid", "to_number", "provider")
    @classmethod
    def strip_required_value(cls, value: str) -> str:
        normalized = (value or "").strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("from_number", "line_phone_number")
    @classmethod
    def strip_optional_value(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class FreeSwitchInboundSipReceipt(BaseModel):
    accepted: bool
    status: str
    provider: str
    call_uuid: str
    to_number: str
    from_number: Optional[str] = None
    agent_found: bool
    agent_id: Optional[uuid.UUID] = None
    agent_name: Optional[str] = None
    call_id: Optional[uuid.UUID] = None
    telephony_leg_id: Optional[str] = None
    error: Optional[str] = None


class TelephonyOutboundCallRequest(BaseModel):
    phone_number: str = Field(..., min_length=7, max_length=20)
    agent_name: str = Field(..., min_length=1, max_length=200)
    mode: str = Field(default="DIRECT")

    @field_validator("phone_number", "agent_name", "mode")
    @classmethod
    def strip_required_values(cls, value: str) -> str:
        normalized = (value or "").strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized


class TelephonyOutboundCallResponse(BaseModel):
    accepted: bool
    provider: str
    agent: str
    mode: str
    status: str
    call_id: Optional[uuid.UUID] = None
    error: Optional[Any] = None


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
    telephony_remote_line_id: Optional[str] = None
    telephony_extension: Optional[str] = None
    telephony_line: Optional[TelephonyLineRead] = None
    suggested_telephony_remote_line_id: Optional[str] = None
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
    telephony_remote_line_id: Optional[str] = None
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
        "telephony_remote_line_id",
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

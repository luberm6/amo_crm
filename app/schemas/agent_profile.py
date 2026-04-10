from __future__ import annotations

from typing import Any, Optional
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


VOICE_STRATEGY_OPTIONS = {
    "disabled",
    "gemini_primary",
    "tts_primary",
    "experimental_hybrid",
}


class AgentProfileBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    is_active: bool = True
    system_prompt: str = Field(min_length=1)
    tone_rules: Optional[str] = None
    business_rules: Optional[str] = None
    sales_objectives: Optional[str] = None
    greeting_text: Optional[str] = None
    transfer_rules: Optional[str] = None
    prohibited_promises: Optional[str] = None
    voice_strategy: str = Field(default="tts_primary")
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name", "system_prompt", "voice_strategy")
    @classmethod
    def strip_required_strings(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("field must not be blank")
        return cleaned

    @field_validator("voice_strategy")
    @classmethod
    def validate_voice_strategy(cls, value: str) -> str:
        if value not in VOICE_STRATEGY_OPTIONS:
            raise ValueError(
                "voice_strategy must be one of: disabled, gemini_primary, "
                "tts_primary, experimental_hybrid"
            )
        return value


class AgentProfileCreate(AgentProfileBase):
    pass


class AgentProfileUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    is_active: Optional[bool] = None
    system_prompt: Optional[str] = Field(default=None, min_length=1)
    tone_rules: Optional[str] = None
    business_rules: Optional[str] = None
    sales_objectives: Optional[str] = None
    greeting_text: Optional[str] = None
    transfer_rules: Optional[str] = None
    prohibited_promises: Optional[str] = None
    voice_strategy: Optional[str] = None
    config: Optional[dict[str, Any]] = None

    @field_validator("name", "system_prompt", "voice_strategy")
    @classmethod
    def strip_optional_strings(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("field must not be blank")
        return cleaned

    @field_validator("voice_strategy")
    @classmethod
    def validate_optional_voice_strategy(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if value not in VOICE_STRATEGY_OPTIONS:
            raise ValueError(
                "voice_strategy must be one of: disabled, gemini_primary, "
                "tts_primary, experimental_hybrid"
            )
        return value


class AgentProfileListItem(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    is_active: bool
    voice_strategy: str
    version: int
    created_at: datetime
    updated_at: datetime


class AgentProfileRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
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
    config: dict[str, Any] = Field(default_factory=dict)
    version: int
    created_at: datetime
    updated_at: datetime
    assembled_prompt_preview: str


class AgentProfileListRead(BaseModel):
    items: list[AgentProfileListItem]
    total: int

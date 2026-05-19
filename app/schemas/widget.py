from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, HttpUrl


class WidgetConfigCreate(BaseModel):
    agent_profile_id: uuid.UUID
    is_active: bool = True
    allowed_domains: list[str] = Field(default_factory=list)
    rate_limit_per_hour: int = Field(default=100, ge=1, le=10000)
    rate_limit_per_ip_per_hour: int = Field(default=10, ge=1, le=1000)
    lead_capture_fields: Optional[dict] = None
    webhook_url: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    custom_greeting: Optional[str] = None
    custom_styles: Optional[dict] = None


class WidgetConfigUpdate(BaseModel):
    is_active: Optional[bool] = None
    allowed_domains: Optional[list[str]] = None
    rate_limit_per_hour: Optional[int] = Field(default=None, ge=1, le=10000)
    rate_limit_per_ip_per_hour: Optional[int] = Field(default=None, ge=1, le=1000)
    lead_capture_fields: Optional[dict] = None
    webhook_url: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    custom_greeting: Optional[str] = None
    custom_styles: Optional[dict] = None


class WidgetConfigRead(BaseModel):
    id: uuid.UUID
    widget_token: str
    agent_profile_id: uuid.UUID
    is_active: bool
    allowed_domains: list[str]
    rate_limit_per_hour: int
    rate_limit_per_ip_per_hour: int
    lead_capture_fields: Optional[dict]
    webhook_url: Optional[str]
    telegram_chat_id: Optional[str]
    custom_greeting: Optional[str]
    custom_styles: Optional[dict]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WidgetPublicConfig(BaseModel):
    """Public config sent to widget.js — no sensitive fields exposed."""
    agent_name: str
    greeting: Optional[str]
    custom_styles: Optional[dict]
    lead_capture_fields: Optional[dict]


class WidgetSessionRequest(BaseModel):
    visitor_id: Optional[str] = None


class WidgetSessionResponse(BaseModel):
    call_id: uuid.UUID
    browser_token: str
    websocket_url: str


class WidgetLeadSubmit(BaseModel):
    call_id: uuid.UUID
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    extra_fields: dict = Field(default_factory=dict)


class WidgetLeadRead(BaseModel):
    id: uuid.UUID
    widget_id: uuid.UUID
    call_id: Optional[uuid.UUID]
    name: Optional[str]
    email: Optional[str]
    phone: Optional[str]
    extra_fields: Optional[dict]
    webhook_delivered: bool
    telegram_delivered: bool
    created_at: datetime

    model_config = {"from_attributes": True}

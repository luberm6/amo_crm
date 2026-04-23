"""
Call DTOs (Data Transfer Objects).
Separation of API contracts from ORM models prevents leaking internal
implementation details and makes API evolution easier.
"""
from __future__ import annotations

from typing import Any, Optional
import uuid
from datetime import datetime

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator

from app.models.call import CallMode, CallStatus
from app.schemas.transcript import TranscriptEntryRead
class CallCreate(BaseModel):
    """Request body for POST /calls."""
    phone: str = Field(
        ...,
        min_length=7,
        max_length=20,
        description="Subscriber phone number. Will be normalized to E.164.",
        examples=["+79991234567", "89991234567"],
        validation_alias=AliasChoices("phone", "phone_number"),
    )
    mode: CallMode = Field(
        default=CallMode.AUTO,
        description="Call engine mode: auto | vapi | direct | browser",
    )
    agent_profile_id: Optional[uuid.UUID] = None
    agent_name: Optional[str] = None
    voice_strategy_override: Optional[str] = None

    @field_validator("phone")
    @classmethod
    def phone_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("phone must not be blank")
        return v.strip()

    @field_validator("mode", mode="before")
    @classmethod
    def normalize_mode(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip().lower()
        return v

    @field_validator("agent_name")
    @classmethod
    def agent_name_not_blank(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("agent_name must not be blank")
        return cleaned

    @field_validator("voice_strategy_override")
    @classmethod
    def voice_strategy_override_not_blank(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("voice_strategy_override must not be blank")
        return cleaned


class CallRead(BaseModel):
    """Response body for GET /calls/{id} and POST /calls."""
    model_config = {"from_attributes": True}
    id: uuid.UUID
    phone: str
    mode: CallMode
    status: CallStatus
    agent_profile_id: Optional[uuid.UUID] = None
    manager_id: Optional[uuid.UUID] = None
    vapi_call_id: Optional[str] = None
    mango_call_id: Optional[str] = None
    route_used: Optional[str] = None
    telephony_leg_id: Optional[str] = None
    # Legacy JSON blob (kept for backward compat); structured entries below
    transcript: Optional[Any] = None
    summary: Optional[str] = None
    sentiment: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None
    # Computed: seconds from created_at to completed_at (or now if still active)
    duration_seconds: Optional[int] = None
    # Structured transcript entries — populated when fetching individual call
    transcript_entries: list[TranscriptEntryRead] = Field(default_factory=list)
    @model_validator(mode="before")
    @classmethod
    def compute_duration(cls, data: Any) -> Any:
        """Compute call duration from timestamps if not provided."""
        if isinstance(data, dict):
            return data
        # ORM object path
        created = getattr(data, "created_at", None)
        completed = getattr(data, "completed_at", None)
        if created and completed:
            delta = completed - created
            data.__dict__["duration_seconds"] = int(delta.total_seconds())
        return data


class CallCreateResponse(BaseModel):
    accepted: bool
    id: Optional[uuid.UUID] = None
    call_id: Optional[uuid.UUID] = None
    phone: Optional[str] = None
    mode: Optional[CallMode] = None
    status: Optional[str] = None
    agent_profile_id: Optional[uuid.UUID] = None
    route_used: Optional[str] = None
    telephony_leg_id: Optional[str] = None
    error: Optional[Any] = None


class CallListItem(BaseModel):
    """Compact representation for list responses."""
    model_config = {"from_attributes": True}
    id: uuid.UUID
    phone: str
    mode: CallMode
    status: CallStatus
    agent_profile_id: Optional[uuid.UUID] = None
    created_at: datetime
class CallActiveList(BaseModel):
    items: list[CallListItem]
    total: int
class CallCardView(BaseModel):
    """
    Compact call view optimised for the Telegram live card.
    Returned by GET /calls/{id}/card.
    Contains only what the bot needs in a single response — avoids N+1 API calls.
    """
    id: uuid.UUID
    phone: str
    mode: CallMode
    status: CallStatus
    agent_profile_id: Optional[uuid.UUID] = None
    is_active: bool
    # None while active, populated after completion
    duration_seconds: Optional[int] = None
    # Set after call ends (from end-of-call-report)
    summary: Optional[str] = None
    sentiment: Optional[str] = None
    # Latest steering directive — shown on the live card
    last_instruction: Optional[str] = None
    # Last N transcript entries (ordered by sequence_num asc)
    # Controlled by ?tail=N query param (default 5, max 20)
    transcript_tail: list[TranscriptEntryRead] = Field(default_factory=list)
    created_at: datetime
    completed_at: Optional[datetime] = None
    # Transfer status from the latest TransferRecord (if any)
    transfer_status: Optional[str] = None
    # Human-readable failure reason from latest TransferRecord
    transfer_failure_reason: Optional[str] = None

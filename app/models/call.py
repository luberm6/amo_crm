"""
Call model — the central entity of the system.
Lifecycle: CREATED → QUEUED → DIALING → RINGING → IN_PROGRESS
           → NEEDS_TRANSFER → TRANSFERRING → MANAGER_BRIEFING
           → CONNECTED_TO_MANAGER → COMPLETED / FAILED / STOPPED
"""
from __future__ import annotations

from typing import Optional, TYPE_CHECKING

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.types import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.agent_profile import AgentProfile
    from app.models.manager import Manager
    from app.models.steering import SteeringInstruction
    from app.models.transfer import TransferRecord


class CallStatus(str, enum.Enum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    DIALING = "DIALING"
    RINGING = "RINGING"
    IN_PROGRESS = "IN_PROGRESS"
    NEEDS_TRANSFER = "NEEDS_TRANSFER"
    TRANSFERRING = "TRANSFERRING"
    MANAGER_BRIEFING = "MANAGER_BRIEFING"
    CONNECTED_TO_MANAGER = "CONNECTED_TO_MANAGER"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"


# Terminal statuses — no further transitions allowed
TERMINAL_STATUSES = {CallStatus.COMPLETED, CallStatus.FAILED, CallStatus.STOPPED}

# Valid status transitions — enforced in CallService for API-initiated changes.
# Webhook processor is more permissive (Vapi can send events out of order).
ALLOWED_TRANSITIONS: dict = {
    CallStatus.CREATED: {
        CallStatus.QUEUED,
        CallStatus.IN_PROGRESS,
        CallStatus.FAILED,
        CallStatus.STOPPED,
    },
    CallStatus.QUEUED: {
        CallStatus.DIALING,
        CallStatus.RINGING,
        CallStatus.IN_PROGRESS,
        CallStatus.FAILED,
        CallStatus.STOPPED,
    },
    CallStatus.DIALING: {
        CallStatus.RINGING,
        CallStatus.IN_PROGRESS,
        CallStatus.FAILED,
        CallStatus.STOPPED,
    },
    CallStatus.RINGING: {
        CallStatus.IN_PROGRESS,
        CallStatus.FAILED,
        CallStatus.STOPPED,
    },
    CallStatus.IN_PROGRESS: {
        CallStatus.NEEDS_TRANSFER,
        CallStatus.COMPLETED,
        CallStatus.FAILED,
        CallStatus.STOPPED,
    },
    CallStatus.NEEDS_TRANSFER: {
        CallStatus.TRANSFERRING,
        CallStatus.COMPLETED,
        CallStatus.FAILED,
        CallStatus.STOPPED,
    },
    CallStatus.TRANSFERRING: {
        CallStatus.MANAGER_BRIEFING,
        CallStatus.FAILED,
        CallStatus.STOPPED,
    },
    CallStatus.MANAGER_BRIEFING: {
        CallStatus.CONNECTED_TO_MANAGER,
        CallStatus.FAILED,
        CallStatus.STOPPED,
    },
    CallStatus.CONNECTED_TO_MANAGER: {
        CallStatus.COMPLETED,
        CallStatus.FAILED,
        CallStatus.STOPPED,
    },
    CallStatus.COMPLETED: set(),
    CallStatus.FAILED: set(),
    CallStatus.STOPPED: set(),
}

# Statuses considered "active" for monitoring and bot /active command
ACTIVE_STATUSES = {
    CallStatus.QUEUED,
    CallStatus.DIALING,
    CallStatus.RINGING,
    CallStatus.IN_PROGRESS,
    CallStatus.NEEDS_TRANSFER,
    CallStatus.TRANSFERRING,
    CallStatus.MANAGER_BRIEFING,
    CallStatus.CONNECTED_TO_MANAGER,
}

# Statuses during or after warm transfer — show reduced bot keyboard
TRANSFER_STATUSES = {
    CallStatus.TRANSFERRING,
    CallStatus.MANAGER_BRIEFING,
    CallStatus.CONNECTED_TO_MANAGER,
}


class CallMode(str, enum.Enum):
    AUTO = "auto"      # System decides (Vapi if available, else Direct)
    VAPI = "vapi"      # Force Vapi engine
    DIRECT = "direct"  # Force Direct engine (future)
    BROWSER = "browser"  # Internal browser sandbox (no Mango/PSTN)


class Call(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "calls"

    # ── Core fields ───────────────────────────────────────────────────────────
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    mode: Mapped[CallMode] = mapped_column(
        String(10), nullable=False, default=CallMode.AUTO
    )
    status: Mapped[CallStatus] = mapped_column(
        String(30), nullable=False, default=CallStatus.CREATED
    )

    # ── Manager assignment ────────────────────────────────────────────────────
    agent_profile_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("agent_profiles.id"), nullable=True
    )
    agent_profile: Mapped[Optional["AgentProfile"]] = relationship(
        "AgentProfile",
        back_populates="calls",
    )

    manager_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("managers.id"), nullable=True
    )
    manager: Mapped[Optional["Manager"]] = relationship(
        "Manager", back_populates="calls"
    )

    # ── External engine references ────────────────────────────────────────────
    # Set when a Vapi call is initiated — used to correlate Vapi webhooks
    vapi_call_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # Set when a Mango session starts (Direct mode: stores Gemini session_id)
    mango_call_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # Which engine route was used: "vapi", "direct", "stub"
    # Set by CallService after initiate_call() returns.
    # Used by RoutingCallEngine to ensure stop/steer/status go to the correct engine.
    route_used: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # Provider-level telephony leg ID (SIP Call-ID or Mango leg UID).
    # Distinct from vapi_call_id / mango_call_id for precise SIP-level tracking.
    telephony_leg_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # ── Conversation data ─────────────────────────────────────────────────────
    # JSON maps to JSONB on Postgres — flexible schema: list of {role, text, ts}
    transcript: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Free-form sentiment label: "positive", "negative", "neutral" or score
    sentiment: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # ── Timestamps ────────────────────────────────────────────────────────────
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Relations ─────────────────────────────────────────────────────────────
    steering_instructions: Mapped[list["SteeringInstruction"]] = relationship(
        "SteeringInstruction", back_populates="call", cascade="all, delete-orphan"
    )
    transfer_records: Mapped[list["TransferRecord"]] = relationship(
        "TransferRecord", back_populates="call", cascade="all, delete-orphan"
    )

    # ── Indexes ───────────────────────────────────────────────────────────────
    __table_args__ = (
        Index("ix_calls_phone", "phone"),
        Index("ix_calls_status", "status"),
        Index("ix_calls_agent_profile_id", "agent_profile_id"),
        Index("ix_calls_vapi_call_id", "vapi_call_id"),
        Index("ix_calls_route_used", "route_used"),
        Index("ix_calls_telephony_leg_id", "telephony_leg_id"),
        # created_at index added via TimestampMixin
    )

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    def is_in_transfer(self) -> bool:
        return self.status in TRANSFER_STATUSES

    def __repr__(self) -> str:
        return f"<Call id={self.id} phone={self.phone} status={self.status}>"

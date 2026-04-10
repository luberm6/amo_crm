"""
VapiEventLog — raw log of every webhook event received from Vapi.
Serves two purposes:
1. Idempotency guard: before processing, check if event already handled
2. Debugging: replay / inspect any event in full fidelity
Written before processing, updated after. Never deleted.
"""
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import DateTime, Index, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON
from app.db.base import Base, UUIDMixin
class VapiEventProcessingStatus(str):
    PENDING = "pending"
    PROCESSED = "processed"
    IGNORED = "ignored"
    ERROR = "error"
class VapiEventLog(UUIDMixin, Base):
    __tablename__ = "vapi_event_logs"
    # Vapi-assigned event ID (present in some event types).
    # Used as idempotency key — if not null and already in DB, skip processing.
    vapi_event_id: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True, unique=True
    )
    # Our internal call ID — may be null if Vapi sends an event before we
    # finish saving the call (race condition guard)
    call_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, nullable=True)
    # Vapi message.type value, e.g. "transcript", "status-update", "end-of-call-report"
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    # Full raw payload from Vapi for auditability
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    # Processing result
    processing_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=VapiEventProcessingStatus.PENDING
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    processed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    __table_args__ = (
        Index("ix_vapi_event_logs_call_id", "call_id"),
        Index("ix_vapi_event_logs_event_type", "event_type"),
        Index("ix_vapi_event_logs_received_at", "received_at"),
    )
    def __repr__(self) -> str:
        return (
            f"<VapiEventLog type={self.event_type} call={self.call_id} "
            f"status={self.processing_status}>"
        )
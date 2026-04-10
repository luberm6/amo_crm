"""
AuditEvent — immutable trail of significant system actions.
Written by services when state changes occur. Never updated or deleted.
"""
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import DateTime, Index, String, Uuid, func
from sqlalchemy.types import JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, UUIDMixin
class AuditEvent(UUIDMixin, Base):
    __tablename__ = "audit_events"
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    # Human-readable action label: "created", "status_changed", "steered", "stopped"
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    # Flexible JSON payload — e.g. {"from": "CREATED", "to": "QUEUED"}
    # JSON maps to JSONB on Postgres, JSON on SQLite — dialect-agnostic
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # Who triggered the action: "system", telegram_id, or service name
    actor: Mapped[str] = mapped_column(String(100), nullable=False, default="system")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (
        Index("ix_audit_events_entity", "entity_type", "entity_id"),
        Index("ix_audit_events_created_at", "created_at"),
    )
    def __repr__(self) -> str:
        return (
            f"<AuditEvent entity={self.entity_type}:{self.entity_id} "
            f"action={self.action}>"
        )
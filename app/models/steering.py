"""
SteeringInstruction — a real-time directive sent to the AI during a call.
Issued by a manager via Telegram (or "system" for automated corrections).
Stored for audit and transcript reconstruction purposes.
"""
import uuid
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Index, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base, UUIDMixin
# Note: no TimestampMixin here — steering is append-only, updated_at not needed
class SteeringInstruction(UUIDMixin, Base):
    __tablename__ = "steering_instructions"
    call_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("calls.id"), nullable=False
    )
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    # "system" or Telegram user ID (as string) of the manager who issued it
    issued_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    call = relationship("Call", back_populates="steering_instructions")
    __table_args__ = (Index("ix_steering_instructions_call_id", "call_id"),)
    def __repr__(self) -> str:
        return f"<SteeringInstruction call_id={self.call_id} by={self.issued_by}>"
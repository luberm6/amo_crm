"""
TranscriptEntry — individual utterance within a call.
Stored as separate rows (not JSON blob on Call) so that:
- Live streaming to Telegram is possible (query by call_id + sequence_num)
- Individual entries can be indexed and queried
- Future ML pipeline can process per-utterance
Written during the call (transcript events from Vapi) and on call completion
(end-of-call-report messages).
"""
import enum
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import DateTime, Index, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON
from app.db.base import Base, UUIDMixin
class TranscriptRole(str, enum.Enum):
    ASSISTANT = "assistant"
    USER = "user"
    SYSTEM = "system"
    TOOL = "tool"  # Function/tool call results
class TranscriptEntry(UUIDMixin, Base):
    __tablename__ = "transcript_entries"
    call_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, nullable=False
    )
    role: Mapped[TranscriptRole] = mapped_column(String(20), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Monotonically increasing within a call — used for ordering + live streaming
    # The frontend/bot queries: WHERE call_id=X AND sequence_num > last_seen
    sequence_num: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Original Vapi message payload — preserved for debugging and replay
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # Wall-clock time of the utterance (from Vapi, or server receipt time)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (
        # Primary access pattern: all entries for a call, in order
        Index("ix_transcript_entries_call_id_seq", "call_id", "sequence_num"),
        # For live streaming: WHERE call_id=X AND sequence_num > N
        Index("ix_transcript_entries_call_id", "call_id"),
    )
    def __repr__(self) -> str:
        return (
            f"<TranscriptEntry call={self.call_id} role={self.role} "
            f"seq={self.sequence_num}>"
        )
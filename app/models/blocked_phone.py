"""
BlockedPhone — deny list for outbound calls.

Any phone number in this table will be rejected at call creation time with a
422 BlockedPhoneError.  Numbers are stored in E.164 format (same as Call.phone).

Typical use cases:
- DNC (Do Not Call) lists
- Internal test numbers that should never receive AI calls
- Numbers flagged as abuse or fraud
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDMixin


class BlockedPhone(UUIDMixin, Base):
    __tablename__ = "blocked_phones"

    # E.164 normalized phone number (e.g. +79991234567)
    phone: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)

    # Human-readable reason for blocking (shown in logs)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Who added this entry
    added_by: Mapped[str] = mapped_column(String(100), nullable=False, default="system")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_blocked_phones_phone", "phone", unique=True),
    )

    def __repr__(self) -> str:
        return f"<BlockedPhone phone={self.phone} reason={self.reason!r}>"

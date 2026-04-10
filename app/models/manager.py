"""
Manager model — a human agent who can receive transferred calls.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.call import Call


class Manager(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "managers"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # E.164 format, e.g. +79991234567
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    # Telegram user ID — used to identify the manager in bot commands
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Availability flag — set False temporarily when manager doesn't answer a transfer
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # UTC timestamp when temporary cooldown ends. NULL means no scheduled restore.
    available_after: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # Lower number = higher priority in manager selection (1=first, 10=last)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    # Optional department filter for targeted transfers (e.g. "sales", "support")
    department: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    calls: Mapped[list["Call"]] = relationship("Call", back_populates="manager")

    __table_args__ = (
        Index("ix_managers_telegram_id", "telegram_id"),
        Index("ix_managers_phone", "phone"),
        Index("ix_managers_department", "department"),
        Index("ix_managers_priority", "priority"),
        Index("ix_managers_available_after", "available_after"),
    )

    def __repr__(self) -> str:
        return (
            f"<Manager id={self.id} name={self.name} "
            f"dept={self.department} priority={self.priority}>"
        )

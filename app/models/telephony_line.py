from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.agent_profile import AgentProfile


class TelephonyLine(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "telephony_lines"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "provider_resource_id",
            name="uq_telephony_lines_provider_resource",
        ),
    )

    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    provider_resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    phone_number: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    extension: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    is_inbound_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_outbound_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    agent_profiles: Mapped[list["AgentProfile"]] = relationship(
        "AgentProfile",
        back_populates="telephony_line",
    )

    def __repr__(self) -> str:
        return (
            f"<TelephonyLine id={self.id} provider={self.provider!r} "
            f"provider_resource_id={self.provider_resource_id!r} phone_number={self.phone_number!r} "
            f"is_active={self.is_active}>"
        )

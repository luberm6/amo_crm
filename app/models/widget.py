from __future__ import annotations

from typing import TYPE_CHECKING, Optional
import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.agent_profile import AgentProfile
    from app.models.call import Call


class WidgetConfig(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "widget_configs"

    widget_token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    agent_profile_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("agent_profiles.id"),
        nullable=False,
        index=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    allowed_domains: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    rate_limit_per_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    rate_limit_per_ip_per_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    lead_capture_fields: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    webhook_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    telegram_chat_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    custom_greeting: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    custom_styles: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    agent_profile: Mapped["AgentProfile"] = relationship("AgentProfile")
    leads: Mapped[list["WidgetLead"]] = relationship(
        "WidgetLead",
        back_populates="widget",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<WidgetConfig id={self.id} token={self.widget_token!r} active={self.is_active}>"


class WidgetLead(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "widget_leads"

    widget_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("widget_configs.id"),
        nullable=False,
        index=True,
    )
    call_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid,
        ForeignKey("calls.id"),
        nullable=True,
        index=True,
    )
    name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    extra_fields: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    webhook_delivered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    telegram_delivered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    widget: Mapped["WidgetConfig"] = relationship("WidgetConfig", back_populates="leads")

    def __repr__(self) -> str:
        return f"<WidgetLead id={self.id} widget_id={self.widget_id} email={self.email!r}>"

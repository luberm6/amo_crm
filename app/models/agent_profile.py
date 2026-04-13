from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.agent_knowledge_binding import AgentKnowledgeBinding
    from app.models.call import Call
    from app.models.telephony_line import TelephonyLine


class AgentProfile(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "agent_profiles"

    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    tone_rules: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    business_rules: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sales_objectives: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    greeting_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    transfer_rules: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    prohibited_promises: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    voice_strategy: Mapped[str] = mapped_column(String(32), nullable=False, default="tts_primary")
    voice_provider: Mapped[str] = mapped_column(String(32), nullable=False, default="elevenlabs")
    telephony_provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    telephony_line_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid,
        ForeignKey("telephony_lines.id"),
        nullable=True,
        index=True,
    )
    telephony_extension: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    calls: Mapped[list["Call"]] = relationship("Call", back_populates="agent_profile")
    telephony_line: Mapped[Optional["TelephonyLine"]] = relationship(
        "TelephonyLine",
        back_populates="agent_profiles",
    )
    knowledge_bindings: Mapped[list["AgentKnowledgeBinding"]] = relationship(
        "AgentKnowledgeBinding",
        back_populates="agent_profile",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<AgentProfile id={self.id} name={self.name!r} "
            f"is_active={self.is_active} version={self.version}>"
        )

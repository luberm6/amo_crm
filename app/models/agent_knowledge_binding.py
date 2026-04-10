from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.agent_profile import AgentProfile
    from app.models.knowledge_document import KnowledgeDocument


class AgentKnowledgeBinding(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "agent_knowledge_bindings"

    agent_profile_id: Mapped[Uuid] = mapped_column(
        Uuid,
        ForeignKey("agent_profiles.id"),
        nullable=False,
        index=True,
    )
    knowledge_document_id: Mapped[Uuid] = mapped_column(
        Uuid,
        ForeignKey("knowledge_documents.id"),
        nullable=False,
        index=True,
    )
    role: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    agent_profile: Mapped["AgentProfile"] = relationship(
        "AgentProfile",
        back_populates="knowledge_bindings",
    )
    knowledge_document: Mapped["KnowledgeDocument"] = relationship(
        "KnowledgeDocument",
        back_populates="bindings",
    )

    def __repr__(self) -> str:
        return (
            f"<AgentKnowledgeBinding id={self.id} agent_profile_id={self.agent_profile_id} "
            f"knowledge_document_id={self.knowledge_document_id}>"
        )

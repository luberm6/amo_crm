from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.agent_knowledge_binding import AgentKnowledgeBinding


class KnowledgeDocument(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_documents"

    title: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[Optional[dict]] = mapped_column("metadata", JSON, nullable=True)

    bindings: Mapped[list["AgentKnowledgeBinding"]] = relationship(
        "AgentKnowledgeBinding",
        back_populates="knowledge_document",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<KnowledgeDocument id={self.id} title={self.title!r} "
            f"category={self.category!r} is_active={self.is_active}>"
        )

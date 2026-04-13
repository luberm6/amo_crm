from __future__ import annotations

from typing import Optional
import uuid

from sqlalchemy import select

from app.models.knowledge_document import KnowledgeDocument
from app.repositories.base import BaseRepository


class KnowledgeDocumentRepository(BaseRepository[KnowledgeDocument]):
    async def list_documents(
        self,
        *,
        category: Optional[str] = None,
        active_only: Optional[bool] = None,
    ) -> list[KnowledgeDocument]:
        stmt = select(KnowledgeDocument).order_by(
            KnowledgeDocument.category.asc(),
            KnowledgeDocument.updated_at.desc(),
            KnowledgeDocument.title.asc(),
        )
        if category:
            stmt = stmt.where(KnowledgeDocument.category == category)
        if active_only is True:
            stmt = stmt.where(KnowledgeDocument.is_active.is_(True))
        elif active_only is False:
            stmt = stmt.where(KnowledgeDocument.is_active.is_(False))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_active(self, document_id: uuid.UUID) -> Optional[KnowledgeDocument]:
        result = await self.session.execute(
            select(KnowledgeDocument)
            .where(KnowledgeDocument.id == document_id)
            .where(KnowledgeDocument.is_active.is_(True))
        )
        return result.scalar_one_or_none()

    async def get_many_active(self, document_ids: list[uuid.UUID]) -> list[KnowledgeDocument]:
        if not document_ids:
            return []
        result = await self.session.execute(
            select(KnowledgeDocument)
            .where(KnowledgeDocument.id.in_(document_ids))
            .where(KnowledgeDocument.is_active.is_(True))
        )
        return list(result.scalars().all())

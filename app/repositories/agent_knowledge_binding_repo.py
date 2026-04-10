from __future__ import annotations

from typing import Optional
import uuid

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.agent_knowledge_binding import AgentKnowledgeBinding
from app.repositories.base import BaseRepository


class AgentKnowledgeBindingRepository(BaseRepository[AgentKnowledgeBinding]):
    async def list_for_agent(self, agent_id: uuid.UUID) -> list[AgentKnowledgeBinding]:
        result = await self.session.execute(
            select(AgentKnowledgeBinding)
            .where(AgentKnowledgeBinding.agent_profile_id == agent_id)
            .options(selectinload(AgentKnowledgeBinding.knowledge_document))
            .order_by(AgentKnowledgeBinding.created_at.desc())
        )
        return list(result.scalars().all())

    async def find_existing(
        self,
        *,
        agent_id: uuid.UUID,
        knowledge_document_id: uuid.UUID,
    ) -> Optional[AgentKnowledgeBinding]:
        result = await self.session.execute(
            select(AgentKnowledgeBinding)
            .where(AgentKnowledgeBinding.agent_profile_id == agent_id)
            .where(AgentKnowledgeBinding.knowledge_document_id == knowledge_document_id)
        )
        return result.scalar_one_or_none()

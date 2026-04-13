from __future__ import annotations

from typing import Optional
import uuid

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.agent_profile import AgentProfile
from app.repositories.base import BaseRepository


class AgentProfileRepository(BaseRepository[AgentProfile]):
    async def list_profiles(self, *, active_only: Optional[bool] = None) -> list[AgentProfile]:
        stmt = select(AgentProfile).order_by(
            AgentProfile.updated_at.desc(),
            AgentProfile.created_at.desc(),
            AgentProfile.name.asc(),
        )
        if active_only is True:
            stmt = stmt.where(AgentProfile.is_active.is_(True))
        elif active_only is False:
            stmt = stmt.where(AgentProfile.is_active.is_(False))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_active(self, agent_id: uuid.UUID) -> Optional[AgentProfile]:
        result = await self.session.execute(
            select(AgentProfile)
            .where(AgentProfile.id == agent_id)
            .where(AgentProfile.is_active.is_(True))
        )
        return result.scalar_one_or_none()

    async def get_with_related(self, agent_id: uuid.UUID) -> Optional[AgentProfile]:
        result = await self.session.execute(
            select(AgentProfile)
            .where(AgentProfile.id == agent_id)
            .options(
                selectinload(AgentProfile.telephony_line),
            )
        )
        return result.scalar_one_or_none()

    async def get_active_by_telephony_line(
        self,
        *,
        telephony_provider: str,
        telephony_line_id: uuid.UUID,
    ) -> Optional[AgentProfile]:
        result = await self.session.execute(
            select(AgentProfile)
            .where(AgentProfile.is_active.is_(True))
            .where(AgentProfile.telephony_provider == telephony_provider)
            .where(AgentProfile.telephony_line_id == telephony_line_id)
        )
        return result.scalar_one_or_none()

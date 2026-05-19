from __future__ import annotations

from typing import Optional
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.widget import WidgetConfig, WidgetLead
from app.repositories.base import BaseRepository


class WidgetRepository(BaseRepository[WidgetConfig]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(WidgetConfig, session)

    async def get_by_token(self, token: str) -> Optional[WidgetConfig]:
        result = await self.session.execute(
            select(WidgetConfig).where(WidgetConfig.widget_token == token)
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[WidgetConfig]:
        result = await self.session.execute(
            select(WidgetConfig).order_by(WidgetConfig.created_at.desc())
        )
        return list(result.scalars().all())


class WidgetLeadRepository(BaseRepository[WidgetLead]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(WidgetLead, session)

    async def get_by_widget(self, widget_id: uuid.UUID) -> list[WidgetLead]:
        result = await self.session.execute(
            select(WidgetLead)
            .where(WidgetLead.widget_id == widget_id)
            .order_by(WidgetLead.created_at.desc())
        )
        return list(result.scalars().all())

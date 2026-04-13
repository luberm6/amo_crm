from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select

from app.models.telephony_line import TelephonyLine
from app.repositories.base import BaseRepository


class TelephonyLineRepository(BaseRepository[TelephonyLine]):
    async def list_lines(
        self,
        *,
        provider: Optional[str] = None,
        active_only: Optional[bool] = None,
    ) -> list[TelephonyLine]:
        stmt = select(TelephonyLine).order_by(
            TelephonyLine.provider.asc(),
            TelephonyLine.phone_number.asc(),
            TelephonyLine.created_at.desc(),
        )
        if provider:
            stmt = stmt.where(TelephonyLine.provider == provider)
        if active_only is True:
            stmt = stmt.where(TelephonyLine.is_active.is_(True))
        elif active_only is False:
            stmt = stmt.where(TelephonyLine.is_active.is_(False))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_provider_resource(
        self,
        *,
        provider: str,
        provider_resource_id: str,
    ) -> Optional[TelephonyLine]:
        result = await self.session.execute(
            select(TelephonyLine)
            .where(TelephonyLine.provider == provider)
            .where(TelephonyLine.provider_resource_id == provider_resource_id)
        )
        return result.scalar_one_or_none()

    async def get_active_by_phone_number(
        self,
        *,
        provider: str,
        phone_number: str,
    ) -> Optional[TelephonyLine]:
        result = await self.session.execute(
            select(TelephonyLine)
            .where(TelephonyLine.provider == provider)
            .where(TelephonyLine.phone_number == phone_number)
            .where(TelephonyLine.is_active.is_(True))
        )
        return result.scalar_one_or_none()

    async def get_many_by_ids(self, ids: list[uuid.UUID]) -> list[TelephonyLine]:
        if not ids:
            return []
        result = await self.session.execute(
            select(TelephonyLine).where(TelephonyLine.id.in_(ids))
        )
        return list(result.scalars().all())

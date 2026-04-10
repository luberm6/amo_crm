from __future__ import annotations

from typing import Optional

from sqlalchemy import select

from app.models.provider_setting import ProviderSetting
from app.repositories.base import BaseRepository


class ProviderSettingRepository(BaseRepository[ProviderSetting]):
    async def get_by_provider(self, provider: str) -> Optional[ProviderSetting]:
        result = await self.session.execute(
            select(ProviderSetting).where(ProviderSetting.provider == provider)
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[ProviderSetting]:
        result = await self.session.execute(
            select(ProviderSetting).order_by(ProviderSetting.provider.asc())
        )
        return list(result.scalars().all())

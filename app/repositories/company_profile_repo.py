from __future__ import annotations

from typing import Optional

from sqlalchemy import select

from app.models.company_profile import CompanyProfile
from app.repositories.base import BaseRepository


class CompanyProfileRepository(BaseRepository[CompanyProfile]):
    async def get_latest_active(self) -> Optional[CompanyProfile]:
        result = await self.session.execute(
            select(CompanyProfile)
            .where(CompanyProfile.is_active.is_(True))
            .order_by(CompanyProfile.updated_at.desc(), CompanyProfile.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

"""
BlockedPhoneRepository — deny list queries.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select

from app.models.blocked_phone import BlockedPhone
from app.repositories.base import BaseRepository


class BlockedPhoneRepository(BaseRepository[BlockedPhone]):

    async def is_blocked(self, phone: str) -> bool:
        """Return True if the E.164 number is on the deny list."""
        result = await self.session.execute(
            select(BlockedPhone).where(BlockedPhone.phone == phone).limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def get_by_phone(self, phone: str) -> Optional[BlockedPhone]:
        """Return the BlockedPhone entry or None."""
        result = await self.session.execute(
            select(BlockedPhone).where(BlockedPhone.phone == phone)
        )
        return result.scalar_one_or_none()

    async def block(
        self, phone: str, reason: Optional[str] = None, added_by: str = "system"
    ) -> BlockedPhone:
        """Add a phone to the deny list (idempotent — returns existing if present)."""
        existing = await self.get_by_phone(phone)
        if existing:
            return existing
        entry = BlockedPhone(phone=phone, reason=reason, added_by=added_by)
        return await self.save(entry)

    async def unblock(self, phone: str) -> bool:
        """Remove a phone from the deny list. Returns True if it existed."""
        entry = await self.get_by_phone(phone)
        if entry is None:
            return False
        await self.delete(entry)
        return True

"""
ManagerRepository — queries on Manager records.

Primary use case: selecting the best available manager for a warm transfer,
ordered by priority (lower number = higher priority).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
import uuid

from sqlalchemy import select, update

from app.models.manager import Manager
from app.repositories.base import BaseRepository


class ManagerRepository(BaseRepository[Manager]):

    async def find_available_managers(
        self,
        department: Optional[str] = None,
    ) -> list[Manager]:
        """
        Return active, available managers ordered by priority ascending.
        1 = highest priority (selected first), 10 = lowest.

        When department is provided, filters to that department only.
        When department is None, returns all active+available managers regardless of dept.
        """
        stmt = (
            select(Manager)
            .where(Manager.is_active.is_(True))
            .where(Manager.is_available.is_(True))
            .order_by(Manager.priority.asc(), Manager.created_at.asc())
        )
        if department is not None:
            stmt = stmt.where(Manager.department == department)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def set_availability(
        self,
        manager_id: uuid.UUID,
        is_available: bool,
    ) -> None:
        """
        Set the is_available flag for a manager.
        Called by TransferService/engine when a manager doesn't answer (False)
        or when the cooldown period has passed and they become available again (True).
        """
        await self.session.execute(
            update(Manager)
            .where(Manager.id == manager_id)
            .values(
                is_available=is_available,
                available_after=None if is_available else Manager.available_after,
            )
        )
        await self.session.flush()

    async def set_temporarily_unavailable(
        self,
        manager_id: uuid.UUID,
        *,
        available_after: datetime,
    ) -> None:
        """
        Mark manager unavailable and persist durable restore deadline.
        """
        await self.session.execute(
            update(Manager)
            .where(Manager.id == manager_id)
            .values(
                is_available=False,
                available_after=available_after,
            )
        )
        await self.session.flush()

    async def try_reserve_available(self, manager_id: uuid.UUID) -> bool:
        """
        Atomically reserve manager for a transfer attempt.

        Returns True only when manager was active+available and now marked unavailable.
        Used to prevent two concurrent transfer flows selecting the same manager.
        """
        result = await self.session.execute(
            update(Manager)
            .where(Manager.id == manager_id)
            .where(Manager.is_active.is_(True))
            .where(Manager.is_available.is_(True))
            .values(is_available=False, available_after=None)
        )
        await self.session.flush()
        return bool(result.rowcount and result.rowcount > 0)

    async def restore_due_managers(self, *, now: Optional[datetime] = None) -> int:
        """
        Restore managers whose cooldown has passed.
        Returns number of restored rows.
        """
        ts = now or datetime.now(timezone.utc)
        result = await self.session.execute(
            update(Manager)
            .where(Manager.is_available.is_(False))
            .where(Manager.available_after.is_not(None))
            .where(Manager.available_after <= ts)
            .values(is_available=True, available_after=None)
        )
        await self.session.flush()
        return int(result.rowcount or 0)

    async def restore_manager_if_due(
        self,
        manager_id: uuid.UUID,
        *,
        now: Optional[datetime] = None,
    ) -> bool:
        ts = now or datetime.now(timezone.utc)
        result = await self.session.execute(
            update(Manager)
            .where(Manager.id == manager_id)
            .where(Manager.is_available.is_(False))
            .where(Manager.available_after.is_not(None))
            .where(Manager.available_after <= ts)
            .values(is_available=True, available_after=None)
        )
        await self.session.flush()
        return bool(result.rowcount and result.rowcount > 0)

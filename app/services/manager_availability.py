from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.models.manager import Manager
from app.repositories.manager_repo import ManagerRepository

log = get_logger(__name__)


async def reconcile_manager_availability_once(session: AsyncSession) -> int:
    """
    Restore due managers whose cooldown deadline has passed.
    """
    repo = ManagerRepository(Manager, session)
    restored = await repo.restore_due_managers()
    return restored


async def reconcile_manager_availability_job(
    session_factory: async_sessionmaker,
) -> int:
    """
    One-shot reconciliation job suitable for startup hooks or periodic tasks.
    """
    async with session_factory() as session:
        restored = await reconcile_manager_availability_once(session)
        await session.commit()
    if restored:
        log.info("manager_availability.reconciled", restored=restored)
    return restored

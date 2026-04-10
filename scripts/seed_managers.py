"""
Seed development managers for local dev and CI.
Idempotent — safe to run multiple times (upsert by telegram_id).

Usage:
  python -m scripts.seed_managers
  # or from project root:
  python scripts/seed_managers.py
"""
from __future__ import annotations

import asyncio
import sys
import os

# Allow running as a script from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.logging import get_logger
from app.db.base import Base
from app.models.manager import Manager

log = get_logger(__name__)

_SEED_MANAGERS = [
    {
        "name": "Алексей Иванов",
        "phone": "+79991110001",
        "telegram_id": 100001,
        "priority": 1,
        "department": "sales",
        "is_active": True,
        "is_available": True,
    },
    {
        "name": "Мария Петрова",
        "phone": "+79992220002",
        "telegram_id": 100002,
        "priority": 2,
        "department": "sales",
        "is_active": True,
        "is_available": True,
    },
    {
        "name": "Дмитрий Сидоров",
        "phone": "+79993330003",
        "telegram_id": 100003,
        "priority": 3,
        "department": "support",
        "is_active": True,
        "is_available": True,
    },
    {
        "name": "Елена Козлова",
        "phone": "+79994440004",
        "telegram_id": 100004,
        "priority": 4,
        "department": "support",
        "is_active": True,
        "is_available": True,
    },
    {
        "name": "Павел Новиков",
        "phone": "+79995550005",
        "telegram_id": 100005,
        "priority": 5,
        "department": None,  # no department — accepts any transfer
        "is_active": True,
        "is_available": True,
    },
]


async def seed(session: AsyncSession) -> None:
    created = 0
    updated = 0

    for data in _SEED_MANAGERS:
        result = await session.execute(
            select(Manager).where(Manager.telegram_id == data["telegram_id"])
        )
        existing = result.scalar_one_or_none()

        if existing is None:
            mgr = Manager(**data)
            session.add(mgr)
            created += 1
            log.info("seed_manager.created", name=data["name"], telegram_id=data["telegram_id"])
        else:
            for key, value in data.items():
                setattr(existing, key, value)
            updated += 1
            log.info("seed_manager.updated", name=data["name"], telegram_id=data["telegram_id"])

    await session.commit()
    print(f"Done: {created} created, {updated} updated.")


async def main() -> None:
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        await seed(session)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

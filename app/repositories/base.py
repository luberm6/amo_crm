"""
Generic async repository.
Provides basic CRUD so concrete repositories only add domain-specific queries.
"""
from __future__ import annotations

from typing import Generic, TypeVar
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.base import Base
ModelT = TypeVar("ModelT", bound=Base)
class BaseRepository(Generic[ModelT]):
    """Generic repository with get / save / delete operations."""
    def __init__(self, model: type[ModelT], session: AsyncSession) -> None:
        self.model = model
        self.session = session
    async def get(self, id: uuid.UUID) -> Optional[ModelT]:
        result = await self.session.execute(
            select(self.model).where(self.model.id == id)
        )
        return result.scalar_one_or_none()
    async def save(self, instance: ModelT) -> ModelT:
        self.session.add(instance)
        await self.session.flush()  # Assigns DB-generated values (e.g. id, created_at)
        await self.session.refresh(instance)
        return instance
    async def delete(self, instance: ModelT) -> None:
        await self.session.delete(instance)
        await self.session.flush()
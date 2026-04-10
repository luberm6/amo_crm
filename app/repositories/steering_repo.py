"""
SteeringRepository — queries on SteeringInstruction records.
Steering instructions are append-only: never updated, never deleted.
The most common access pattern is "what was the last instruction for this call?"
used in the Telegram live card and engine reconciliation.
"""
from __future__ import annotations
from typing import Optional
import uuid
from sqlalchemy import select
from app.models.steering import SteeringInstruction
from app.repositories.base import BaseRepository
class SteeringRepository(BaseRepository[SteeringInstruction]):
    async def get_last_for_call(self, call_id: uuid.UUID) -> Optional[SteeringInstruction]:
        """
        Return the most recently issued instruction for a call.
        Used by the Telegram live card to show current AI directive.
        """
        result = await self.session.execute(
            select(SteeringInstruction)
            .where(SteeringInstruction.call_id == call_id)
            .order_by(SteeringInstruction.created_at.desc(), SteeringInstruction.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
    async def get_all_for_call(
        self, call_id: uuid.UUID
    ) -> list[SteeringInstruction]:
        """
        Return all instructions for a call in chronological order.
        Used for the instruction history log and engine replay.
        """
        result = await self.session.execute(
            select(SteeringInstruction)
            .where(SteeringInstruction.call_id == call_id)
            .order_by(SteeringInstruction.created_at.asc())
        )
        return list(result.scalars().all())
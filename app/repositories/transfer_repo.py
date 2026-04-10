"""
TransferRepository — queries on TransferRecord records.

Transfer records are append-only: never deleted.
The most common access patterns are:
  - get_latest_for_call: show transfer status in bot card and manager context endpoint
  - get_active_for_call: find ongoing (non-terminal) transfer for state machine ops
"""
from __future__ import annotations

from typing import Optional
import uuid

from sqlalchemy import select

from app.models.transfer import TransferRecord, TERMINAL_TRANSFER_STATUSES
from app.repositories.base import BaseRepository


class TransferRepository(BaseRepository[TransferRecord]):

    async def get_latest_for_call(
        self, call_id: uuid.UUID
    ) -> Optional[TransferRecord]:
        """
        Return the most recent transfer record for a call regardless of status.
        Used for manager-context endpoint and bot card display.
        """
        result = await self.session.execute(
            select(TransferRecord)
            .where(TransferRecord.call_id == call_id)
            .order_by(TransferRecord.created_at.desc(), TransferRecord.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_active_for_call(
        self, call_id: uuid.UUID
    ) -> Optional[TransferRecord]:
        """
        Return the most recent non-terminal transfer record for a call.
        Used by the transfer state machine to resume or check in-progress transfers.
        """
        result = await self.session.execute(
            select(TransferRecord)
            .where(TransferRecord.call_id == call_id)
            .where(TransferRecord.status.not_in(
                [s.value for s in TERMINAL_TRANSFER_STATUSES]
            ))
            .order_by(TransferRecord.created_at.desc(), TransferRecord.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

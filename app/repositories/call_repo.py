"""
CallRepository — domain-specific queries on top of the generic base.
"""
from __future__ import annotations
from typing import Optional
import uuid
from sqlalchemy import select, update
from app.models.call import ACTIVE_STATUSES, Call, CallStatus
from app.repositories.base import BaseRepository
class CallRepository(BaseRepository[Call]):
    async def get_for_update(self, call_id: uuid.UUID) -> Optional[Call]:
        """
        Fetch a Call row with SELECT ... FOR UPDATE to prevent concurrent modifications.

        Used by TransferService to prevent double-transfer race conditions:
        both concurrent requests read the call, but only one proceeds — the
        other sees the updated status after the lock is released.

        Note: SQLite (used in tests) silently ignores FOR UPDATE.
        Production (PostgreSQL) enforces a row-level lock within the transaction.
        """
        result = await self.session.execute(
            select(Call).where(Call.id == call_id).with_for_update()
        )
        return result.scalar_one_or_none()
    """All Call-related DB queries live here, not in service or handler."""
    async def get_active_calls(self) -> list[Call]:
        """Return all calls that are not in a terminal status, ordered newest first."""
        result = await self.session.execute(
            select(Call)
            .where(Call.status.in_(list(ACTIVE_STATUSES)))
            .order_by(Call.created_at.desc())
        )
        return list(result.scalars().all())
    async def get_by_vapi_id(self, vapi_call_id: str) -> Optional[Call]:
        """Look up a call by its Vapi-assigned ID (used in webhook handlers)."""
        result = await self.session.execute(
            select(Call).where(Call.vapi_call_id == vapi_call_id)
        )
        return result.scalar_one_or_none()
    async def get_by_mango_call_id(self, mango_call_id: str) -> Optional[Call]:
        """
        Look up a call by its Direct-mode session ID (stored in mango_call_id).
        Used by startup reconciliation to find orphaned Direct sessions.
        """
        result = await self.session.execute(
            select(Call).where(Call.mango_call_id == mango_call_id)
        )
        return result.scalar_one_or_none()

    async def get_by_telephony_leg_id(self, telephony_leg_id: str) -> Optional[Call]:
        """Look up a call by provider leg ID used by telephony control-plane."""
        result = await self.session.execute(
            select(Call).where(Call.telephony_leg_id == telephony_leg_id)
        )
        return result.scalar_one_or_none()

    async def update_status(
        self, call_id: uuid.UUID, new_status: CallStatus
    ) -> None:
        """Bulk-update a single call's status without loading the full ORM object."""
        await self.session.execute(
            update(Call).where(Call.id == call_id).values(status=new_status)
        )

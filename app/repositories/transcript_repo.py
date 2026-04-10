"""
TranscriptRepository — reads and writes TranscriptEntry rows.
Key access pattern: get all entries for a call in sequence order.
For future live streaming: get entries after a given sequence_num.
"""
from __future__ import annotations
from typing import Optional
import uuid
from sqlalchemy import func, select
from app.models.transcript import TranscriptEntry, TranscriptRole
from app.repositories.base import BaseRepository
class TranscriptRepository(BaseRepository[TranscriptEntry]):
    async def get_by_call(self, call_id: uuid.UUID) -> list[TranscriptEntry]:
        """Return all transcript entries for a call, ordered by sequence_num."""
        result = await self.session.execute(
            select(TranscriptEntry)
            .where(TranscriptEntry.call_id == call_id)
            .order_by(TranscriptEntry.sequence_num.asc())
        )
        return list(result.scalars().all())
    async def get_after_sequence(
        self, call_id: uuid.UUID, after_seq: int
    ) -> list[TranscriptEntry]:
        """
        Return entries newer than a given sequence_num.
        Used for live streaming: bot polls with last_seen_seq and gets new lines.
        """
        result = await self.session.execute(
            select(TranscriptEntry)
            .where(
                TranscriptEntry.call_id == call_id,
                TranscriptEntry.sequence_num > after_seq,
            )
            .order_by(TranscriptEntry.sequence_num.asc())
        )
        return list(result.scalars().all())
    async def next_sequence_num(self, call_id: uuid.UUID) -> int:
        """
        Return the next sequence number for a call (max + 1, or 0).
        Combines committed DB state with the session identity map so that
        flushed-but-uncommitted rows (visible in the same transaction on
        Postgres but not always on SQLite/aiosqlite) are also counted.
        """
        result = await self.session.execute(
            select(func.max(TranscriptEntry.sequence_num)).where(
                TranscriptEntry.call_id == call_id
            )
        )
        db_max: Optional[int] = result.scalar_one_or_none()
        # Also scan the session identity map for flushed-but-not-committed rows.
        # This is a no-op on Postgres (queries see flushed data) but is needed
        # for SQLite in tests where the driver uses separate read/write contexts.
        identity_seqs = [
            obj.sequence_num
            for obj in self.session.identity_map.values()
            if isinstance(obj, TranscriptEntry)
            and obj.call_id == call_id
            and obj.sequence_num is not None
        ]
        best = max(
            db_max if db_max is not None else -1,
            max(identity_seqs) if identity_seqs else -1,
        )
        return best + 1
    async def append(
        self,
        call_id: uuid.UUID,
        role: TranscriptRole,
        text: str,
        raw_payload: Optional[dict] = None,
    ) -> TranscriptEntry:
        """Append a single entry with auto-assigned sequence number."""
        seq = await self.next_sequence_num(call_id)
        entry = TranscriptEntry(
            call_id=call_id,
            role=role,
            text=text,
            sequence_num=seq,
            raw_payload=raw_payload,
        )
        return await self.save(entry)
    async def bulk_append(
        self,
        call_id: uuid.UUID,
        entries: list[dict],
    ) -> list[TranscriptEntry]:
        """
        Append multiple entries in order, assigning contiguous sequence numbers.
        Each entry dict: {role, text, raw_payload?}
        Used when processing end-of-call-report messages array.
        """
        start_seq = await self.next_sequence_num(call_id)
        saved = []
        for i, entry_data in enumerate(entries):
            entry = TranscriptEntry(
                call_id=call_id,
                role=entry_data["role"],
                text=entry_data["text"],
                sequence_num=start_seq + i,
                raw_payload=entry_data.get("raw_payload"),
            )
            self.session.add(entry)
            saved.append(entry)
        await self.session.flush()
        return saved
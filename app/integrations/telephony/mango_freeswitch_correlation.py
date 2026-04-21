from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from redis.asyncio import Redis

from app.core.logging import get_logger
from app.core.redis_client import get_redis
from app.integrations.telephony.base import TelephonyLegState

log = get_logger(__name__)

_TTL_SECONDS = 24 * 60 * 60


@dataclass
class CorrelatedLegSnapshot:
    mango_leg_id: str
    call_id: Optional[str] = None
    freeswitch_uuid: Optional[str] = None
    freeswitch_session_id: Optional[str] = None
    mango_state: Optional[TelephonyLegState] = None
    freeswitch_state: Optional[TelephonyLegState] = None
    effective_state: Optional[TelephonyLegState] = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    raw_mango_event: Optional[dict] = None
    raw_freeswitch_event: Optional[dict] = None


class AbstractMangoFreeSwitchCorrelationStore:
    async def upsert_mapping(
        self,
        *,
        mango_leg_id: str,
        call_id: Optional[str] = None,
        freeswitch_uuid: Optional[str] = None,
        freeswitch_session_id: Optional[str] = None,
    ) -> CorrelatedLegSnapshot:
        raise NotImplementedError

    async def set_mango_state(
        self,
        *,
        mango_leg_id: str,
        state: TelephonyLegState,
        call_id: Optional[str] = None,
        raw_event: Optional[dict] = None,
    ) -> CorrelatedLegSnapshot:
        raise NotImplementedError

    async def set_freeswitch_state(
        self,
        *,
        mango_leg_id: str,
        state: TelephonyLegState,
        freeswitch_uuid: Optional[str] = None,
        freeswitch_session_id: Optional[str] = None,
        raw_event: Optional[dict] = None,
    ) -> CorrelatedLegSnapshot:
        raise NotImplementedError

    async def get(self, mango_leg_id: str) -> Optional[CorrelatedLegSnapshot]:
        raise NotImplementedError

    async def find_mango_leg_id_by_call_id(self, call_id: str) -> Optional[str]:
        raise NotImplementedError

    async def get_effective_state(self, mango_leg_id: str) -> Optional[TelephonyLegState]:
        snap = await self.get(mango_leg_id)
        return snap.effective_state if snap else None


class InMemoryMangoFreeSwitchCorrelationStore(AbstractMangoFreeSwitchCorrelationStore):
    def __init__(self) -> None:
        self._data: dict[str, CorrelatedLegSnapshot] = {}
        self._call_to_leg: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def upsert_mapping(
        self,
        *,
        mango_leg_id: str,
        call_id: Optional[str] = None,
        freeswitch_uuid: Optional[str] = None,
        freeswitch_session_id: Optional[str] = None,
    ) -> CorrelatedLegSnapshot:
        async with self._lock:
            current = self._data.get(mango_leg_id) or CorrelatedLegSnapshot(mango_leg_id=mango_leg_id)
            if call_id is not None:
                current.call_id = call_id
                self._call_to_leg[call_id] = mango_leg_id
            if freeswitch_uuid is not None:
                current.freeswitch_uuid = freeswitch_uuid
            if freeswitch_session_id is not None:
                current.freeswitch_session_id = freeswitch_session_id
            current.effective_state = _effective_state(current.mango_state, current.freeswitch_state)
            current.updated_at = datetime.now(timezone.utc)
            self._data[mango_leg_id] = current
            return current

    async def set_mango_state(
        self,
        *,
        mango_leg_id: str,
        state: TelephonyLegState,
        call_id: Optional[str] = None,
        raw_event: Optional[dict] = None,
    ) -> CorrelatedLegSnapshot:
        async with self._lock:
            current = self._data.get(mango_leg_id) or CorrelatedLegSnapshot(mango_leg_id=mango_leg_id)
            current.mango_state = state
            if call_id is not None:
                current.call_id = call_id
            if raw_event is not None:
                current.raw_mango_event = raw_event
            current.effective_state = _effective_state(current.mango_state, current.freeswitch_state)
            current.updated_at = datetime.now(timezone.utc)
            self._data[mango_leg_id] = current
            return current

    async def set_freeswitch_state(
        self,
        *,
        mango_leg_id: str,
        state: TelephonyLegState,
        freeswitch_uuid: Optional[str] = None,
        freeswitch_session_id: Optional[str] = None,
        raw_event: Optional[dict] = None,
    ) -> CorrelatedLegSnapshot:
        async with self._lock:
            current = self._data.get(mango_leg_id) or CorrelatedLegSnapshot(mango_leg_id=mango_leg_id)
            current.freeswitch_state = state
            if freeswitch_uuid is not None:
                current.freeswitch_uuid = freeswitch_uuid
            if freeswitch_session_id is not None:
                current.freeswitch_session_id = freeswitch_session_id
            if raw_event is not None:
                current.raw_freeswitch_event = raw_event
            current.effective_state = _effective_state(current.mango_state, current.freeswitch_state)
            current.updated_at = datetime.now(timezone.utc)
            self._data[mango_leg_id] = current
            return current

    async def get(self, mango_leg_id: str) -> Optional[CorrelatedLegSnapshot]:
        return self._data.get(mango_leg_id)

    async def find_mango_leg_id_by_call_id(self, call_id: str) -> Optional[str]:
        return self._call_to_leg.get(call_id)


class RedisMangoFreeSwitchCorrelationStore(AbstractMangoFreeSwitchCorrelationStore):
    _KEY = "mango:fs:corr:{}"
    _CALL_KEY = "mango:fs:corr:call:{}"

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def upsert_mapping(
        self,
        *,
        mango_leg_id: str,
        call_id: Optional[str] = None,
        freeswitch_uuid: Optional[str] = None,
        freeswitch_session_id: Optional[str] = None,
    ) -> CorrelatedLegSnapshot:
        fields: dict[str, str] = {"updated_at": datetime.now(timezone.utc).isoformat()}
        if call_id is not None:
            fields["call_id"] = call_id
        if freeswitch_uuid is not None:
            fields["freeswitch_uuid"] = freeswitch_uuid
        if freeswitch_session_id is not None:
            fields["freeswitch_session_id"] = freeswitch_session_id
        await self._merge_and_store(mango_leg_id, fields)
        if call_id is not None:
            await self._redis.set(self._CALL_KEY.format(call_id), mango_leg_id, ex=_TTL_SECONDS)
        snap = await self.get(mango_leg_id)
        assert snap is not None
        return snap

    async def set_mango_state(
        self,
        *,
        mango_leg_id: str,
        state: TelephonyLegState,
        call_id: Optional[str] = None,
        raw_event: Optional[dict] = None,
    ) -> CorrelatedLegSnapshot:
        fields: dict[str, str] = {
            "mango_state": state.value,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if call_id is not None:
            fields["call_id"] = call_id
        if raw_event is not None:
            fields["raw_mango_event"] = json.dumps(raw_event, ensure_ascii=False)
        await self._merge_and_store(mango_leg_id, fields)
        snap = await self.get(mango_leg_id)
        assert snap is not None
        return snap

    async def set_freeswitch_state(
        self,
        *,
        mango_leg_id: str,
        state: TelephonyLegState,
        freeswitch_uuid: Optional[str] = None,
        freeswitch_session_id: Optional[str] = None,
        raw_event: Optional[dict] = None,
    ) -> CorrelatedLegSnapshot:
        fields: dict[str, str] = {
            "freeswitch_state": state.value,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if freeswitch_uuid is not None:
            fields["freeswitch_uuid"] = freeswitch_uuid
        if freeswitch_session_id is not None:
            fields["freeswitch_session_id"] = freeswitch_session_id
        if raw_event is not None:
            fields["raw_freeswitch_event"] = json.dumps(raw_event, ensure_ascii=False)
        await self._merge_and_store(mango_leg_id, fields)
        snap = await self.get(mango_leg_id)
        assert snap is not None
        return snap

    async def get(self, mango_leg_id: str) -> Optional[CorrelatedLegSnapshot]:
        key = self._KEY.format(mango_leg_id)
        data = await self._redis.hgetall(key)
        if not data:
            return None
        mango_state = _state_or_none(data.get("mango_state"))
        fs_state = _state_or_none(data.get("freeswitch_state"))
        raw_mango = data.get("raw_mango_event")
        raw_fs = data.get("raw_freeswitch_event")
        return CorrelatedLegSnapshot(
            mango_leg_id=mango_leg_id,
            call_id=data.get("call_id"),
            freeswitch_uuid=data.get("freeswitch_uuid"),
            freeswitch_session_id=data.get("freeswitch_session_id"),
            mango_state=mango_state,
            freeswitch_state=fs_state,
            effective_state=_state_or_none(data.get("effective_state")),
            updated_at=_parse_dt(data.get("updated_at")),
            raw_mango_event=json.loads(raw_mango) if raw_mango else None,
            raw_freeswitch_event=json.loads(raw_fs) if raw_fs else None,
        )

    async def find_mango_leg_id_by_call_id(self, call_id: str) -> Optional[str]:
        leg_id = await self._redis.get(self._CALL_KEY.format(call_id))
        return str(leg_id) if leg_id else None

    async def _merge_and_store(self, mango_leg_id: str, patch: dict[str, str]) -> None:
        key = self._KEY.format(mango_leg_id)
        current = await self._redis.hgetall(key)
        merged = dict(current or {})
        merged.update(patch)
        effective = _effective_state(
            _state_or_none(merged.get("mango_state")),
            _state_or_none(merged.get("freeswitch_state")),
        )
        if effective is not None:
            merged["effective_state"] = effective.value
        await self._redis.hset(key, mapping=merged)
        await self._redis.expire(key, _TTL_SECONDS)


def _state_or_none(raw: Optional[str]) -> Optional[TelephonyLegState]:
    if not raw:
        return None
    try:
        return TelephonyLegState(str(raw))
    except Exception:
        return None


def _parse_dt(raw: Optional[str]) -> datetime:
    if not raw:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime.now(timezone.utc)


def _effective_state(
    mango_state: Optional[TelephonyLegState],
    fs_state: Optional[TelephonyLegState],
) -> Optional[TelephonyLegState]:
    states = {s for s in (mango_state, fs_state) if s is not None}
    if not states:
        return None
    if TelephonyLegState.BRIDGED in states:
        return TelephonyLegState.BRIDGED
    if TelephonyLegState.ANSWERED in states:
        return TelephonyLegState.ANSWERED
    if TelephonyLegState.TERMINATED in states:
        return TelephonyLegState.TERMINATED
    if TelephonyLegState.FAILED in states:
        return TelephonyLegState.FAILED
    if TelephonyLegState.RINGING in states:
        return TelephonyLegState.RINGING
    if TelephonyLegState.INITIATING in states:
        return TelephonyLegState.INITIATING
    if TelephonyLegState.TERMINATING in states:
        return TelephonyLegState.TERMINATING
    return None


_fallback_store = InMemoryMangoFreeSwitchCorrelationStore()


def get_mango_freeswitch_correlation_store() -> AbstractMangoFreeSwitchCorrelationStore:
    redis = get_redis()
    if redis is not None:
        return RedisMangoFreeSwitchCorrelationStore(redis)
    log.warning(
        "mango_freeswitch_correlation.in_memory_fallback",
        message="Redis unavailable, Mango↔FreeSWITCH correlation state will not survive restart.",
    )
    return _fallback_store

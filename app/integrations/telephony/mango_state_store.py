from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from redis.asyncio import Redis

from app.core.logging import get_logger
from app.integrations.telephony.base import TelephonyLegState

log = get_logger(__name__)

_LEG_TTL_SECONDS = 24 * 60 * 60
_EVENT_TTL_SECONDS = 24 * 60 * 60
_OP_TTL_SECONDS = 2 * 60 * 60


@dataclass
class MangoLegSnapshot:
    leg_id: str
    state: TelephonyLegState
    updated_at: datetime
    call_id: Optional[str] = None
    transfer_id: Optional[str] = None
    role: Optional[str] = None
    raw_event: Optional[dict] = field(default=None)


class AbstractMangoLegStateStore:
    async def set_leg_state(
        self,
        leg_id: str,
        state: TelephonyLegState,
        *,
        call_id: Optional[str] = None,
        transfer_id: Optional[str] = None,
        role: Optional[str] = None,
        raw_event: Optional[dict] = None,
    ) -> MangoLegSnapshot:
        raise NotImplementedError

    async def get_leg_state(self, leg_id: str) -> Optional[MangoLegSnapshot]:
        raise NotImplementedError

    async def set_leg_context(
        self,
        leg_id: str,
        *,
        call_id: Optional[str] = None,
        transfer_id: Optional[str] = None,
        role: Optional[str] = None,
    ) -> None:
        raise NotImplementedError

    async def set_bridge_status(self, bridge_key: str, status: str) -> None:
        raise NotImplementedError

    async def get_bridge_status(self, bridge_key: str) -> Optional[str]:
        raise NotImplementedError

    async def set_whisper_status(self, leg_id: str, status: str) -> None:
        raise NotImplementedError

    async def get_whisper_status(self, leg_id: str) -> Optional[str]:
        raise NotImplementedError

    async def mark_event_seen(self, event_key: str) -> bool:
        raise NotImplementedError

    async def wait_for_leg_state(
        self,
        leg_id: str,
        accepted: set[TelephonyLegState],
        failed: set[TelephonyLegState],
        *,
        timeout: float,
        poll_interval: float = 0.4,
        poll_fallback: Optional[Callable[[], Awaitable[Optional[TelephonyLegState]]]] = None,
    ) -> Optional[TelephonyLegState]:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            snap = await self.get_leg_state(leg_id)
            if snap:
                if snap.state in accepted:
                    return snap.state
                if snap.state in failed:
                    return snap.state
            if poll_fallback is not None:
                try:
                    polled_state = await poll_fallback()
                    if polled_state is not None:
                        await self.set_leg_state(leg_id, polled_state)
                except Exception as exc:
                    log.debug(
                        "mango_state_store.poll_fallback_error",
                        leg_id=leg_id,
                        error=str(exc),
                    )
            await asyncio.sleep(poll_interval)
        return None

    async def wait_for_bridge_status(
        self,
        bridge_key: str,
        accepted: set[str],
        failed: set[str],
        *,
        timeout: float,
        poll_interval: float = 0.4,
    ) -> Optional[str]:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            status = await self.get_bridge_status(bridge_key)
            if status in accepted or status in failed:
                return status
            await asyncio.sleep(poll_interval)
        return None

    async def wait_for_whisper_status(
        self,
        leg_id: str,
        accepted: set[str],
        failed: set[str],
        *,
        timeout: float,
        poll_interval: float = 0.4,
    ) -> Optional[str]:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            status = await self.get_whisper_status(leg_id)
            if status in accepted or status in failed:
                return status
            await asyncio.sleep(poll_interval)
        return None


class InMemoryMangoLegStateStore(AbstractMangoLegStateStore):
    def __init__(
        self,
        *,
        legs: Optional[dict[str, dict]] = None,
        bridge_ops: Optional[dict[str, str]] = None,
        whisper_ops: Optional[dict[str, str]] = None,
        seen_events: Optional[set[str]] = None,
    ) -> None:
        self._legs = legs if legs is not None else {}
        self._bridge_ops = bridge_ops if bridge_ops is not None else {}
        self._whisper_ops = whisper_ops if whisper_ops is not None else {}
        self._seen_events = seen_events if seen_events is not None else set()
        self._lock = asyncio.Lock()

    async def set_leg_state(
        self,
        leg_id: str,
        state: TelephonyLegState,
        *,
        call_id: Optional[str] = None,
        transfer_id: Optional[str] = None,
        role: Optional[str] = None,
        raw_event: Optional[dict] = None,
    ) -> MangoLegSnapshot:
        now = datetime.now(timezone.utc)
        async with self._lock:
            current = self._legs.get(leg_id, {})
            self._legs[leg_id] = {
                "state": state.value,
                "updated_at": now.isoformat(),
                "call_id": call_id if call_id is not None else current.get("call_id"),
                "transfer_id": (
                    transfer_id if transfer_id is not None else current.get("transfer_id")
                ),
                "role": role if role is not None else current.get("role"),
                "raw_event": raw_event if raw_event is not None else current.get("raw_event"),
            }
            return _snapshot_from_dict(leg_id, self._legs[leg_id])

    async def get_leg_state(self, leg_id: str) -> Optional[MangoLegSnapshot]:
        data = self._legs.get(leg_id)
        if not data:
            return None
        return _snapshot_from_dict(leg_id, data)

    async def set_leg_context(
        self,
        leg_id: str,
        *,
        call_id: Optional[str] = None,
        transfer_id: Optional[str] = None,
        role: Optional[str] = None,
    ) -> None:
        async with self._lock:
            current = self._legs.get(leg_id, {})
            if "state" not in current:
                current["state"] = TelephonyLegState.INITIATING.value
                current["updated_at"] = datetime.now(timezone.utc).isoformat()
            if call_id is not None:
                current["call_id"] = call_id
            if transfer_id is not None:
                current["transfer_id"] = transfer_id
            if role is not None:
                current["role"] = role
            self._legs[leg_id] = current

    async def set_bridge_status(self, bridge_key: str, status: str) -> None:
        async with self._lock:
            self._bridge_ops[bridge_key] = status

    async def get_bridge_status(self, bridge_key: str) -> Optional[str]:
        return self._bridge_ops.get(bridge_key)

    async def set_whisper_status(self, leg_id: str, status: str) -> None:
        async with self._lock:
            self._whisper_ops[leg_id] = status

    async def get_whisper_status(self, leg_id: str) -> Optional[str]:
        return self._whisper_ops.get(leg_id)

    async def mark_event_seen(self, event_key: str) -> bool:
        async with self._lock:
            if event_key in self._seen_events:
                return False
            self._seen_events.add(event_key)
            return True


class RedisMangoLegStateStore(AbstractMangoLegStateStore):
    _LEG_KEY = "mango:leg:{}"
    _EVENT_KEY = "mango:event:{}"
    _BRIDGE_KEY = "mango:bridge:{}"
    _WHISPER_KEY = "mango:whisper:{}"

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def set_leg_state(
        self,
        leg_id: str,
        state: TelephonyLegState,
        *,
        call_id: Optional[str] = None,
        transfer_id: Optional[str] = None,
        role: Optional[str] = None,
        raw_event: Optional[dict] = None,
    ) -> MangoLegSnapshot:
        key = self._LEG_KEY.format(leg_id)
        raw_str = json.dumps(raw_event, ensure_ascii=False) if raw_event is not None else None
        now = datetime.now(timezone.utc).isoformat()
        fields = {"state": state.value, "updated_at": now}
        if call_id is not None:
            fields["call_id"] = call_id
        if transfer_id is not None:
            fields["transfer_id"] = transfer_id
        if role is not None:
            fields["role"] = role
        if raw_str is not None:
            fields["raw_event"] = raw_str
        await self._redis.hset(key, mapping=fields)
        await self._redis.expire(key, _LEG_TTL_SECONDS)
        data = await self._redis.hgetall(key)
        return _snapshot_from_dict(leg_id, data)

    async def get_leg_state(self, leg_id: str) -> Optional[MangoLegSnapshot]:
        key = self._LEG_KEY.format(leg_id)
        data = await self._redis.hgetall(key)
        if not data:
            return None
        return _snapshot_from_dict(leg_id, data)

    async def set_leg_context(
        self,
        leg_id: str,
        *,
        call_id: Optional[str] = None,
        transfer_id: Optional[str] = None,
        role: Optional[str] = None,
    ) -> None:
        key = self._LEG_KEY.format(leg_id)
        exists = await self._redis.exists(key)
        fields: dict[str, str] = {}
        if not exists:
            fields["state"] = TelephonyLegState.INITIATING.value
            fields["updated_at"] = datetime.now(timezone.utc).isoformat()
        if call_id is not None:
            fields["call_id"] = call_id
        if transfer_id is not None:
            fields["transfer_id"] = transfer_id
        if role is not None:
            fields["role"] = role
        if fields:
            await self._redis.hset(key, mapping=fields)
            await self._redis.expire(key, _LEG_TTL_SECONDS)

    async def set_bridge_status(self, bridge_key: str, status: str) -> None:
        key = self._BRIDGE_KEY.format(bridge_key)
        await self._redis.set(key, status, ex=_OP_TTL_SECONDS)

    async def get_bridge_status(self, bridge_key: str) -> Optional[str]:
        key = self._BRIDGE_KEY.format(bridge_key)
        return await self._redis.get(key)

    async def set_whisper_status(self, leg_id: str, status: str) -> None:
        key = self._WHISPER_KEY.format(leg_id)
        await self._redis.set(key, status, ex=_OP_TTL_SECONDS)

    async def get_whisper_status(self, leg_id: str) -> Optional[str]:
        key = self._WHISPER_KEY.format(leg_id)
        return await self._redis.get(key)

    async def mark_event_seen(self, event_key: str) -> bool:
        key = self._EVENT_KEY.format(event_key)
        created = await self._redis.set(key, "1", ex=_EVENT_TTL_SECONDS, nx=True)
        return bool(created)


def _snapshot_from_dict(leg_id: str, data: dict) -> MangoLegSnapshot:
    state_raw = str(data.get("state", TelephonyLegState.FAILED.value))
    try:
        state = TelephonyLegState(state_raw)
    except ValueError:
        state = TelephonyLegState.FAILED
    updated_at_raw = data.get("updated_at")
    if updated_at_raw:
        try:
            updated_at = datetime.fromisoformat(updated_at_raw)
        except Exception:
            updated_at = datetime.now(timezone.utc)
    else:
        updated_at = datetime.now(timezone.utc)
    raw_event = data.get("raw_event")
    if isinstance(raw_event, str):
        try:
            raw_event = json.loads(raw_event)
        except Exception:
            raw_event = None
    return MangoLegSnapshot(
        leg_id=leg_id,
        state=state,
        updated_at=updated_at,
        call_id=data.get("call_id"),
        transfer_id=data.get("transfer_id"),
        role=data.get("role"),
        raw_event=raw_event if isinstance(raw_event, dict) else None,
    )

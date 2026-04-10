"""
Session store abstraction for DirectSessionManager.

Provides a distributed coordination layer for Direct mode sessions, replacing
the naive in-process dict that couldn't survive restarts or multi-worker deploys.

Design:
  SessionMetadata — persistent session data (survives restarts, stored in Redis)
  Ownership lock  — which worker currently holds the live WS connection (TTL-based lease)
  Active set      — fast lookup of all session IDs that are not yet terminated
  Steering channel — pub/sub for routing instructions to the owning worker

Implementations:
  InMemorySessionStore — single-process, no external deps (dev / tests / Redis-unavailable)
  RedisSessionStore    — multi-worker production coordination via Redis

The LIVE parts of a session (asyncio.Task, WebSocket, asyncio.Queue) must live
in exactly one process and cannot be serialised.  This store manages only what
CAN be distributed: metadata, ownership leases, and steering delivery.
"""
from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import AsyncIterator, Optional

from app.core.logging import get_logger

log = get_logger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

LOCK_TTL_SECONDS: int = 30       # Ownership lock expires after 30 s with no heartbeat
HEARTBEAT_INTERVAL: int = 10     # Heartbeat renews lock every 10 s
SESSION_META_TTL: int = 86_400   # Metadata kept for 24 h after termination


# ── Domain types ───────────────────────────────────────────────────────────────

class SessionStatus(str, Enum):
    ACTIVE = "active"
    TERMINATED = "terminated"   # Graceful shutdown
    FAILED = "failed"            # Process died, reconciler cleaned up


@dataclass
class SessionMetadata:
    session_id: str
    call_id: str
    phone: str
    status: SessionStatus
    worker_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "call_id": self.call_id,
            "phone": self.phone,
            "status": self.status.value,
            "worker_id": self.worker_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SessionMetadata":
        return cls(
            session_id=data["session_id"],
            call_id=data["call_id"],
            phone=data["phone"],
            status=SessionStatus(data["status"]),
            worker_id=data.get("worker_id", "unknown"),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
        )


@dataclass
class SessionStoreStats:
    active_count: int
    orphaned_count: int   # Active sessions with no live owner (lock expired)
    total_tracked: int    # All sessions in metadata store


# ── Abstract interface ─────────────────────────────────────────────────────────

class AbstractSessionStore(ABC):
    """
    Distributed session coordination interface.

    All methods are async so implementations can do I/O without blocking.
    Implementations must be safe for concurrent coroutines in a single event loop.
    They do NOT need to be safe across OS threads.
    """

    # ── Metadata CRUD ─────────────────────────────────────────────────────────

    @abstractmethod
    async def create(self, meta: SessionMetadata) -> None:
        """Persist new session metadata."""

    @abstractmethod
    async def get(self, session_id: str) -> Optional[SessionMetadata]:
        """Retrieve session metadata. Returns None if not found."""

    @abstractmethod
    async def update_status(self, session_id: str, status: SessionStatus) -> bool:
        """
        Update session status + updated_at timestamp.
        Returns True if the session existed and was updated.
        """

    # ── Ownership lock (distributed lease) ───────────────────────────────────

    @abstractmethod
    async def acquire_lock(
        self,
        session_id: str,
        worker_id: str,
        ttl: int = LOCK_TTL_SECONDS,
    ) -> bool:
        """
        Atomically claim exclusive ownership of a session (SET NX EX semantics).

        Returns True if this worker now owns the session.
        Returns False if another worker already holds the lock.

        The lock expires automatically after `ttl` seconds.  The owner must call
        renew_lock() periodically (heartbeat) to prevent expiry.
        """

    @abstractmethod
    async def renew_lock(
        self,
        session_id: str,
        worker_id: str,
        ttl: int = LOCK_TTL_SECONDS,
    ) -> bool:
        """
        Extend the ownership TTL (heartbeat operation).

        Only succeeds if this worker still holds the lock.
        Returns False if the lock expired or was taken by someone else.
        """

    @abstractmethod
    async def release_lock(self, session_id: str, worker_id: str) -> bool:
        """
        Release ownership.  Only releases if this worker currently owns it.
        Returns True if released, False if not our lock.
        """

    @abstractmethod
    async def get_lock_owner(self, session_id: str) -> Optional[str]:
        """Return the worker_id that currently owns this session, or None."""

    # ── Active sessions set ───────────────────────────────────────────────────

    @abstractmethod
    async def add_to_active(self, session_id: str) -> None:
        """Register a session as active (added at creation, removed at termination)."""

    @abstractmethod
    async def remove_from_active(self, session_id: str) -> None:
        """Remove a session from the active set."""

    @abstractmethod
    async def get_active_ids(self) -> list[str]:
        """Return all session IDs currently in the active set."""

    # ── Steering pub/sub ──────────────────────────────────────────────────────

    @abstractmethod
    async def publish_steering(self, session_id: str, instruction: str) -> None:
        """Publish a steering instruction to the session's coordination channel."""

    @abstractmethod
    async def subscribe_steering(self, session_id: str) -> AsyncIterator[str]:
        """
        Async-iterate over steering instructions published to this session's channel.

        For InMemory: yields from an asyncio.Queue.
        For Redis: subscribes to a pub/sub channel and yields messages.

        Implementations MUST be cancellable (respect asyncio.CancelledError).
        """

    # ── Observability ─────────────────────────────────────────────────────────

    @abstractmethod
    async def get_stats(self) -> SessionStoreStats:
        """Return current store stats for health checks and monitoring."""


# ── InMemorySessionStore ───────────────────────────────────────────────────────

class InMemorySessionStore(AbstractSessionStore):
    """
    Single-process in-memory implementation.

    Used for:
    - Unit tests (no external deps, fast, deterministic)
    - Development environments without Redis
    - Graceful degradation when Redis is unavailable

    Multi-worker simulation: two InMemorySessionStore instances that share the
    same underlying dicts can simulate multi-worker behaviour in tests.
    """

    def __init__(self) -> None:
        self._meta: dict[str, SessionMetadata] = {}
        self._locks: dict[str, str] = {}        # session_id → worker_id
        self._active: set[str] = set()
        self._steer_queues: dict[str, asyncio.Queue] = {}
        self._lock = asyncio.Lock()             # Protect concurrent coroutines

    # ── Metadata ──────────────────────────────────────────────────────────────

    async def create(self, meta: SessionMetadata) -> None:
        async with self._lock:
            self._meta[meta.session_id] = meta

    async def get(self, session_id: str) -> Optional[SessionMetadata]:
        return self._meta.get(session_id)

    async def update_status(self, session_id: str, status: SessionStatus) -> bool:
        async with self._lock:
            m = self._meta.get(session_id)
            if m is None:
                return False
            m.status = status
            m.updated_at = datetime.now(timezone.utc)
            return True

    # ── Ownership ─────────────────────────────────────────────────────────────

    async def acquire_lock(
        self,
        session_id: str,
        worker_id: str,
        ttl: int = LOCK_TTL_SECONDS,
    ) -> bool:
        async with self._lock:
            if session_id in self._locks:
                return False                    # Already owned
            self._locks[session_id] = worker_id
            return True

    async def renew_lock(
        self,
        session_id: str,
        worker_id: str,
        ttl: int = LOCK_TTL_SECONDS,
    ) -> bool:
        async with self._lock:
            return self._locks.get(session_id) == worker_id  # No TTL in memory

    async def release_lock(self, session_id: str, worker_id: str) -> bool:
        async with self._lock:
            if self._locks.get(session_id) != worker_id:
                return False
            del self._locks[session_id]
            return True

    async def get_lock_owner(self, session_id: str) -> Optional[str]:
        return self._locks.get(session_id)

    # ── Active set ────────────────────────────────────────────────────────────

    async def add_to_active(self, session_id: str) -> None:
        async with self._lock:
            self._active.add(session_id)

    async def remove_from_active(self, session_id: str) -> None:
        async with self._lock:
            self._active.discard(session_id)

    async def get_active_ids(self) -> list[str]:
        async with self._lock:
            return list(self._active)

    # ── Steering ──────────────────────────────────────────────────────────────

    async def publish_steering(self, session_id: str, instruction: str) -> None:
        async with self._lock:
            if session_id not in self._steer_queues:
                self._steer_queues[session_id] = asyncio.Queue()
        await self._steer_queues[session_id].put(instruction)

    async def subscribe_steering(self, session_id: str) -> AsyncIterator[str]:
        async with self._lock:
            if session_id not in self._steer_queues:
                self._steer_queues[session_id] = asyncio.Queue()
        q = self._steer_queues[session_id]
        try:
            while True:
                instruction = await q.get()
                yield instruction
        except asyncio.CancelledError:
            pass

    # ── Stats ─────────────────────────────────────────────────────────────────

    async def get_stats(self) -> SessionStoreStats:
        async with self._lock:
            active_ids = list(self._active)
            orphaned = sum(1 for sid in active_ids if sid not in self._locks)
            return SessionStoreStats(
                active_count=len(active_ids),
                orphaned_count=orphaned,
                total_tracked=len(self._meta),
            )

    # ── Test helpers ──────────────────────────────────────────────────────────

    def _simulate_lock_expiry(self, session_id: str) -> None:
        """
        Test helper: simulate TTL expiry for a session lock.
        Use this to test orphan detection / restart recovery without real timers.
        """
        self._locks.pop(session_id, None)


# ── RedisSessionStore ──────────────────────────────────────────────────────────

class RedisSessionStore(AbstractSessionStore):
    """
    Redis-backed session store for production multi-worker deployments.

    Key schema:
      session:meta:{session_id}       — HASH, metadata fields, TTL=24h
      session:lock:{session_id}       — STRING, worker_id, TTL=30s (heartbeat-renewed)
      sessions:active                  — SET, all active session_ids
      session:steer:{session_id}      — Pub/Sub channel (no persistence)

    Ownership lock uses SET NX EX (atomic).
    Renewal uses a Lua script to prevent renewing a lock we no longer hold.
    Release uses a Lua script (check owner → delete, atomic).
    """

    _META_KEY = "session:meta:{}"
    _LOCK_KEY = "session:lock:{}"
    _ACTIVE_KEY = "sessions:active"
    _STEER_CHANNEL = "session:steer:{}"

    # Lua: renew lock only if we still own it
    _RENEW_LUA = """
local cur = redis.call('GET', KEYS[1])
if cur == ARGV[1] then
    redis.call('EXPIRE', KEYS[1], ARGV[2])
    return 1
end
return 0
"""

    # Lua: release lock only if we own it
    _RELEASE_LUA = """
local cur = redis.call('GET', KEYS[1])
if cur == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""

    def __init__(self, redis) -> None:
        self._redis = redis

    async def create(self, meta: SessionMetadata) -> None:
        key = self._META_KEY.format(meta.session_id)
        await self._redis.hset(key, mapping=meta.to_dict())
        await self._redis.expire(key, SESSION_META_TTL)

    async def get(self, session_id: str) -> Optional[SessionMetadata]:
        key = self._META_KEY.format(session_id)
        data = await self._redis.hgetall(key)
        if not data:
            return None
        return SessionMetadata.from_dict(data)

    async def update_status(self, session_id: str, status: SessionStatus) -> bool:
        key = self._META_KEY.format(session_id)
        if not await self._redis.exists(key):
            return False
        await self._redis.hset(key, mapping={
            "status": status.value,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        return True

    async def acquire_lock(
        self,
        session_id: str,
        worker_id: str,
        ttl: int = LOCK_TTL_SECONDS,
    ) -> bool:
        key = self._LOCK_KEY.format(session_id)
        result = await self._redis.set(key, worker_id, ex=ttl, nx=True)
        return result is not None

    async def renew_lock(
        self,
        session_id: str,
        worker_id: str,
        ttl: int = LOCK_TTL_SECONDS,
    ) -> bool:
        key = self._LOCK_KEY.format(session_id)
        result = await self._redis.eval(
            self._RENEW_LUA, 1, key, worker_id, str(ttl)
        )
        return bool(result)

    async def release_lock(self, session_id: str, worker_id: str) -> bool:
        key = self._LOCK_KEY.format(session_id)
        result = await self._redis.eval(self._RELEASE_LUA, 1, key, worker_id)
        return bool(result)

    async def get_lock_owner(self, session_id: str) -> Optional[str]:
        key = self._LOCK_KEY.format(session_id)
        return await self._redis.get(key)

    async def add_to_active(self, session_id: str) -> None:
        await self._redis.sadd(self._ACTIVE_KEY, session_id)

    async def remove_from_active(self, session_id: str) -> None:
        await self._redis.srem(self._ACTIVE_KEY, session_id)

    async def get_active_ids(self) -> list[str]:
        members = await self._redis.smembers(self._ACTIVE_KEY)
        return list(members)

    async def publish_steering(self, session_id: str, instruction: str) -> None:
        channel = self._STEER_CHANNEL.format(session_id)
        await self._redis.publish(channel, instruction)

    async def subscribe_steering(self, session_id: str) -> AsyncIterator[str]:
        channel = self._STEER_CHANNEL.format(session_id)
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(channel)
        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    yield message["data"]
        except asyncio.CancelledError:
            pass
        finally:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()
            except Exception:
                pass

    async def get_stats(self) -> SessionStoreStats:
        active_ids = await self.get_active_ids()
        orphaned = 0
        for sid in active_ids:
            owner = await self.get_lock_owner(sid)
            if owner is None:
                orphaned += 1
        return SessionStoreStats(
            active_count=len(active_ids),
            orphaned_count=orphaned,
            total_tracked=len(active_ids),
        )

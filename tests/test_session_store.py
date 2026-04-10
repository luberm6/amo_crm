"""
Tests for AbstractSessionStore implementations (InMemorySessionStore).

Tests cover all behaviours that must work the same way for both
InMemorySessionStore and RedisSessionStore — written against the abstract
interface so they are portable.

Test coverage:
  1.  create + get round-trip
  2.  get returns None for unknown session
  3.  update_status reflects immediately
  4.  update_status on unknown session returns False
  5.  acquire_lock grants ownership
  6.  acquire_lock is exclusive — second worker cannot take the lock
  7.  renew_lock succeeds for the owner
  8.  renew_lock fails for a non-owner
  9.  release_lock removes ownership
  10. release_lock by non-owner returns False
  11. get_lock_owner after expiry simulation returns None
  12. active set operations (add / remove / list)
  13. publish_steering / subscribe_steering round-trip
  14. stats reflect active + orphaned counts
  15. multi-worker: two workers share the same store, only one can acquire
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest

from app.integrations.direct.session_store import (
    InMemorySessionStore,
    SessionMetadata,
    SessionStatus,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _meta(session_id: str = None, worker_id: str = "worker-a") -> SessionMetadata:
    return SessionMetadata(
        session_id=session_id or f"sess-{uuid.uuid4().hex[:8]}",
        call_id=str(uuid.uuid4()),
        phone="+79991234567",
        status=SessionStatus.ACTIVE,
        worker_id=worker_id,
    )


@pytest.fixture
def store() -> InMemorySessionStore:
    return InMemorySessionStore()


# ── 1. create + get ────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_create_and_get(store: InMemorySessionStore) -> None:
    """Created metadata is retrievable by session_id."""
    m = _meta("sess-1")
    await store.create(m)
    result = await store.get("sess-1")
    assert result is not None
    assert result.session_id == "sess-1"
    assert result.phone == "+79991234567"
    assert result.status == SessionStatus.ACTIVE


# ── 2. get unknown ────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_get_unknown_returns_none(store: InMemorySessionStore) -> None:
    result = await store.get("no-such-session")
    assert result is None


# ── 3. update_status ──────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_update_status(store: InMemorySessionStore) -> None:
    m = _meta("sess-2")
    await store.create(m)
    updated = await store.update_status("sess-2", SessionStatus.TERMINATED)
    assert updated is True
    result = await store.get("sess-2")
    assert result.status == SessionStatus.TERMINATED


# ── 4. update_status unknown ──────────────────────────────────────────────────

@pytest.mark.anyio
async def test_update_status_unknown_returns_false(store: InMemorySessionStore) -> None:
    updated = await store.update_status("ghost-session", SessionStatus.FAILED)
    assert updated is False


# ── 5. acquire_lock grants ownership ─────────────────────────────────────────

@pytest.mark.anyio
async def test_acquire_lock_grants_ownership(store: InMemorySessionStore) -> None:
    acquired = await store.acquire_lock("sess-3", "worker-a")
    assert acquired is True
    owner = await store.get_lock_owner("sess-3")
    assert owner == "worker-a"


# ── 6. acquire_lock is exclusive ──────────────────────────────────────────────

@pytest.mark.anyio
async def test_acquire_lock_exclusive(store: InMemorySessionStore) -> None:
    """Second worker cannot claim a lock already held by another."""
    await store.acquire_lock("sess-4", "worker-a")
    acquired_by_b = await store.acquire_lock("sess-4", "worker-b")
    assert acquired_by_b is False
    owner = await store.get_lock_owner("sess-4")
    assert owner == "worker-a"  # Original owner unchanged


# ── 7. renew_lock succeeds for owner ─────────────────────────────────────────

@pytest.mark.anyio
async def test_renew_lock_owner_succeeds(store: InMemorySessionStore) -> None:
    await store.acquire_lock("sess-5", "worker-a")
    renewed = await store.renew_lock("sess-5", "worker-a")
    assert renewed is True
    assert await store.get_lock_owner("sess-5") == "worker-a"


# ── 8. renew_lock fails for non-owner ────────────────────────────────────────

@pytest.mark.anyio
async def test_renew_lock_non_owner_fails(store: InMemorySessionStore) -> None:
    await store.acquire_lock("sess-6", "worker-a")
    renewed = await store.renew_lock("sess-6", "worker-b")
    assert renewed is False


# ── 9. release_lock ───────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_release_lock(store: InMemorySessionStore) -> None:
    await store.acquire_lock("sess-7", "worker-a")
    released = await store.release_lock("sess-7", "worker-a")
    assert released is True
    assert await store.get_lock_owner("sess-7") is None


# ── 10. release by non-owner ─────────────────────────────────────────────────

@pytest.mark.anyio
async def test_release_lock_non_owner_returns_false(store: InMemorySessionStore) -> None:
    await store.acquire_lock("sess-8", "worker-a")
    released = await store.release_lock("sess-8", "worker-b")
    assert released is False
    assert await store.get_lock_owner("sess-8") == "worker-a"  # Still owned by a


# ── 11. lock expiry simulation ────────────────────────────────────────────────

@pytest.mark.anyio
async def test_lock_expiry_simulation(store: InMemorySessionStore) -> None:
    """
    After _simulate_lock_expiry(), the session becomes orphaned.
    This mimics what happens when a worker's heartbeat stops and the TTL expires.
    """
    await store.acquire_lock("sess-9", "worker-a")
    store._simulate_lock_expiry("sess-9")       # Simulate TTL expiry
    assert await store.get_lock_owner("sess-9") is None


# ── 12. active set ────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_active_set_operations(store: InMemorySessionStore) -> None:
    await store.add_to_active("s1")
    await store.add_to_active("s2")
    active = await store.get_active_ids()
    assert set(active) == {"s1", "s2"}

    await store.remove_from_active("s1")
    active = await store.get_active_ids()
    assert active == ["s2"]


# ── 13. steering pub/sub ──────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_steering_pubsub(store: InMemorySessionStore) -> None:
    """
    publish_steering followed by subscribe_steering delivers the instruction.
    We use a timeout to avoid hanging if the message is never published.
    """
    received: list[str] = []

    async def subscriber():
        async for instruction in store.subscribe_steering("sess-pub"):
            received.append(instruction)
            break                           # Consume one message and stop

    # Start subscriber, then publish
    task = asyncio.create_task(subscriber())
    await asyncio.sleep(0)                  # Yield to let subscriber start
    await store.publish_steering("sess-pub", "Уточни бюджет")
    await asyncio.wait_for(task, timeout=1.0)

    assert received == ["Уточни бюджет"]


# ── 14. stats ─────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_stats_reflect_orphans(store: InMemorySessionStore) -> None:
    """
    Stats show orphaned count correctly when some sessions have no lock owner.
    """
    # Session A: active and owned
    await store.add_to_active("sa")
    await store.acquire_lock("sa", "worker-a")

    # Session B: active but orphaned (lock expired)
    await store.add_to_active("sb")
    await store.acquire_lock("sb", "worker-b")
    store._simulate_lock_expiry("sb")

    stats = await store.get_stats()
    assert stats.active_count == 2
    assert stats.orphaned_count == 1


# ── 15. multi-worker simulation ───────────────────────────────────────────────

@pytest.mark.anyio
async def test_multi_worker_only_one_acquires() -> None:
    """
    Two workers sharing the same InMemorySessionStore cannot both own a session.
    Simulates two worker processes using the same Redis (shared backend).
    """
    shared_store = InMemorySessionStore()
    # Both workers try to acquire the lock concurrently
    results = await asyncio.gather(
        shared_store.acquire_lock("shared-sess", "worker-alpha"),
        shared_store.acquire_lock("shared-sess", "worker-beta"),
    )
    # Exactly one should succeed
    assert results.count(True) == 1
    assert results.count(False) == 1

    owner = await shared_store.get_lock_owner("shared-sess")
    assert owner in ("worker-alpha", "worker-beta")

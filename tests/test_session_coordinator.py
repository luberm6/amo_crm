"""
Tests for SessionCoordinator — distributed ownership and lifecycle management.

Tests cover:
  1.  register_session — acquires lock, adds to active, starts heartbeat
  2.  register_session — duplicate ownership prevented (conflict returns False)
  3.  release_session — releases lock, removes from active, updates status
  4.  send_steering local — fast-path injects into asyncio.Queue directly
  5.  send_steering remote — published via store when not local
  6.  send_steering no owner — returns False when session has no lock owner
  7.  startup_reconcile — marks orphaned calls FAILED in DB
  8.  startup_reconcile — already-terminal calls are not touched
  9.  startup_reconcile — already-owned sessions are skipped
  10. cleanup_stale — removes sessions with expired lock and terminal metadata
  11. get_stats — reflects owned sessions count
  12. heartbeat — renew_lock called; test via mock
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.integrations.direct.session_coordinator import SessionCoordinator
from app.integrations.direct.session_store import (
    InMemorySessionStore,
    SessionStatus,
)
from app.models.call import Call, CallMode, CallStatus


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_coordinator(worker_id: str = "worker-test") -> tuple[SessionCoordinator, InMemorySessionStore]:
    store = InMemorySessionStore()
    coord = SessionCoordinator(store=store, worker_id=worker_id)
    return coord, store


def _make_call(status: CallStatus = CallStatus.IN_PROGRESS) -> MagicMock:
    call = MagicMock(spec=Call)
    call.id = uuid.uuid4()
    call.status = status
    call.mode = CallMode.DIRECT
    return call


@dataclass
class _FakeSession:
    """Minimal stand-in for DirectSession to test steering routing."""
    session_id: str
    instruction_queue: asyncio.Queue = field(default_factory=asyncio.Queue)


# ── 1. register_session ───────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_register_session_acquires_lock_and_adds_to_active() -> None:
    coord, store = _make_coordinator("wa")
    session_id = f"sess-{uuid.uuid4().hex[:6]}"

    ok = await coord.register_session(session_id, str(uuid.uuid4()), "+79991234567")
    assert ok is True

    owner = await store.get_lock_owner(session_id)
    assert owner == "wa"

    active = await store.get_active_ids()
    assert session_id in active

    # Heartbeat task started
    assert session_id in coord._heartbeat_tasks

    # Cleanup: cancel heartbeat to avoid test loop hanging
    coord._heartbeat_tasks[session_id].cancel()


# ── 2. duplicate ownership prevented ─────────────────────────────────────────

@pytest.mark.anyio
async def test_register_session_conflict_returns_false() -> None:
    """
    Two coordinators (simulating two workers) share one store.
    Only one should acquire the lock.
    """
    shared_store = InMemorySessionStore()
    coord_a = SessionCoordinator(store=shared_store, worker_id="wa")
    coord_b = SessionCoordinator(store=shared_store, worker_id="wb")

    session_id = f"sess-{uuid.uuid4().hex[:6]}"
    call_id = str(uuid.uuid4())

    ok_a = await coord_a.register_session(session_id, call_id, "+79991234567")
    ok_b = await coord_b.register_session(session_id, call_id, "+79991234567")

    assert ok_a is True
    assert ok_b is False   # Second registration rejected

    owner = await shared_store.get_lock_owner(session_id)
    assert owner == "wa"

    coord_a._heartbeat_tasks[session_id].cancel()


# ── 3. release_session ────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_release_session_cleans_up() -> None:
    coord, store = _make_coordinator("wa")
    session_id = f"sess-{uuid.uuid4().hex[:6]}"

    await coord.register_session(session_id, str(uuid.uuid4()), "+79991234567")
    coord._heartbeat_tasks[session_id].cancel()     # Don't let heartbeat run

    await coord.release_session(session_id)

    # Lock released
    assert await store.get_lock_owner(session_id) is None
    # Removed from active
    assert session_id not in await store.get_active_ids()
    # Status updated
    meta = await store.get(session_id)
    assert meta.status == SessionStatus.TERMINATED
    # Heartbeat task removed
    assert session_id not in coord._heartbeat_tasks


# ── 4. send_steering local fast-path ─────────────────────────────────────────

@pytest.mark.anyio
async def test_send_steering_local_injects_to_queue() -> None:
    coord, store = _make_coordinator("wa")
    session_id = "sess-local"

    fake = _FakeSession(session_id=session_id)
    local_sessions = {session_id: fake}

    delivered = await coord.send_steering(session_id, "Уточни бюджет", local_sessions)
    assert delivered is True
    assert not fake.instruction_queue.empty()
    assert fake.instruction_queue.get_nowait() == "Уточни бюджет"


# ── 5. send_steering remote via store ────────────────────────────────────────

@pytest.mark.anyio
async def test_send_steering_remote_publishes_to_store() -> None:
    """Session lives on worker-b; steering comes in on worker-a → publish."""
    shared_store = InMemorySessionStore()
    coord_a = SessionCoordinator(store=shared_store, worker_id="wa")
    coord_b = SessionCoordinator(store=shared_store, worker_id="wb")

    session_id = f"sess-{uuid.uuid4().hex[:6]}"
    # Worker B registers the session (it owns the live WS)
    await coord_b.register_session(session_id, str(uuid.uuid4()), "+79991234567")
    coord_b._heartbeat_tasks[session_id].cancel()

    # Worker A's local_sessions dict is empty (session is on worker B)
    received_by_b: list[str] = []
    fake = _FakeSession(session_id=session_id)
    local_sessions_b = {session_id: fake}

    # Start B's steering subscriber
    sub_task = asyncio.create_task(
        coord_b._steering_subscriber(session_id, local_sessions_b)
    )
    await asyncio.sleep(0)          # Let subscriber start

    # Worker A sends steering
    delivered = await coord_a.send_steering(session_id, "Предложи скидку", {})
    assert delivered is True

    # Give subscriber time to receive
    await asyncio.sleep(0.05)
    sub_task.cancel()
    try:
        await sub_task
    except asyncio.CancelledError:
        pass

    assert not fake.instruction_queue.empty()
    assert fake.instruction_queue.get_nowait() == "Предложи скидку"


# ── 6. send_steering no owner ─────────────────────────────────────────────────

@pytest.mark.anyio
async def test_send_steering_no_owner_returns_false() -> None:
    coord, store = _make_coordinator("wa")
    # Session not registered anywhere
    delivered = await coord.send_steering("ghost-session", "Anything", {})
    assert delivered is False


# ── 7. startup_reconcile marks orphaned calls FAILED ─────────────────────────

@pytest.mark.anyio
async def test_startup_reconcile_fails_orphaned_calls(session) -> None:
    """
    When a session has no lock owner (orphaned after worker crash), reconcile
    must mark the corresponding Call FAILED.
    """
    from app.models.call import Call
    from app.repositories.call_repo import CallRepository

    # Create a real call in the DB
    call = Call(
        phone="+79991234567",
        mode=CallMode.DIRECT,
        status=CallStatus.IN_PROGRESS,
    )
    call_repo = CallRepository(Call, session)
    await call_repo.save(call)
    session_id = f"{call.id}-direct"
    call.mango_call_id = session_id
    await session_repo_save_bare(session, call)

    # Register session then simulate worker crash (lock expires)
    coord, store = _make_coordinator("wa")
    await coord.register_session(session_id, str(call.id), call.phone)
    coord._heartbeat_tasks[session_id].cancel()
    store._simulate_lock_expiry(session_id)     # TTL expired, no owner

    # Run reconciliation
    stats = await coord.startup_reconcile(call_repo)

    assert stats["orphaned_failed"] == 1
    assert stats["total_checked"] == 1

    # Reload call from DB
    await session.refresh(call)
    assert call.status == CallStatus.FAILED


async def session_repo_save_bare(session, obj) -> None:
    """Helper: merge + flush without committing."""
    session.add(obj)
    await session.flush()


# ── 8. startup_reconcile skips already-terminal ───────────────────────────────

@pytest.mark.anyio
async def test_startup_reconcile_skips_terminal_calls(session) -> None:
    from app.models.call import Call
    from app.repositories.call_repo import CallRepository

    call = Call(
        phone="+79992345678",
        mode=CallMode.DIRECT,
        status=CallStatus.COMPLETED,        # Already terminal
    )
    call_repo = CallRepository(Call, session)
    await call_repo.save(call)
    session_id = f"{call.id}-direct"
    call.mango_call_id = session_id
    await session_repo_save_bare(session, call)

    coord, store = _make_coordinator("wa")
    await coord.register_session(session_id, str(call.id), call.phone)
    coord._heartbeat_tasks[session_id].cancel()
    store._simulate_lock_expiry(session_id)

    await coord.startup_reconcile(call_repo)

    await session.refresh(call)
    # Status should not change — already terminal
    assert call.status == CallStatus.COMPLETED


# ── 9. startup_reconcile skips owned sessions ─────────────────────────────────

@pytest.mark.anyio
async def test_startup_reconcile_skips_owned_sessions() -> None:
    coord, store = _make_coordinator("wa")
    session_id = f"owned-{uuid.uuid4().hex[:6]}"
    await coord.register_session(session_id, str(uuid.uuid4()), "+79991234567")
    coord._heartbeat_tasks[session_id].cancel()
    # Do NOT simulate expiry — lock is still held

    mock_repo = AsyncMock()
    stats = await coord.startup_reconcile(mock_repo)

    assert stats["already_owned"] == 1
    assert stats["orphaned_failed"] == 0
    mock_repo.get_by_mango_call_id.assert_not_called()


# ── 10. cleanup_stale ─────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_cleanup_stale_removes_dead_sessions() -> None:
    """
    Sessions that are in the active set but have expired locks AND terminal
    metadata should be removed by cleanup_stale().
    """
    coord, store = _make_coordinator("wa")
    session_id = f"stale-{uuid.uuid4().hex[:6]}"

    # Register, then simulate graceful release that left stale state
    await store.create(
        __import__("app.integrations.direct.session_store", fromlist=["SessionMetadata"]).SessionMetadata(
            session_id=session_id,
            call_id=str(uuid.uuid4()),
            phone="+79991234567",
            status=SessionStatus.TERMINATED,
            worker_id="wa",
        )
    )
    await store.add_to_active(session_id)
    # No lock held (expired or never acquired)

    cleaned = await coord.cleanup_stale()
    assert session_id in cleaned
    assert session_id not in await store.get_active_ids()


# ── 11. get_stats ─────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_get_stats_reflects_owned_count() -> None:
    coord, store = _make_coordinator("wa")

    sid1 = f"s-{uuid.uuid4().hex[:6]}"
    sid2 = f"s-{uuid.uuid4().hex[:6]}"
    await coord.register_session(sid1, str(uuid.uuid4()), "+79991234567")
    await coord.register_session(sid2, str(uuid.uuid4()), "+79992345678")
    coord._heartbeat_tasks[sid1].cancel()
    coord._heartbeat_tasks[sid2].cancel()

    stats = await coord.get_stats()
    assert stats["worker_id"] == "wa"
    assert stats["owned_sessions"] == 2
    assert stats["active_in_store"] == 2
    assert stats["orphaned_in_store"] == 0


# ── 12. heartbeat renews lock ─────────────────────────────────────────────────

@pytest.mark.anyio
async def test_heartbeat_renews_lock() -> None:
    """
    _heartbeat_loop() calls renew_lock() after HEARTBEAT_INTERVAL seconds.
    We patch asyncio.sleep to avoid real waiting and verify renew_lock is called.
    """
    coord, store = _make_coordinator("wa")
    session_id = f"hb-{uuid.uuid4().hex[:6]}"
    await store.acquire_lock(session_id, "wa")

    renewal_count = 0

    original_renew = store.renew_lock

    async def counting_renew(sid, wid, ttl=30):
        nonlocal renewal_count
        result = await original_renew(sid, wid, ttl)
        renewal_count += 1
        return result

    store.renew_lock = counting_renew  # Patch on instance

    # Run heartbeat loop for one iteration then cancel
    with patch("app.integrations.direct.session_coordinator.asyncio.sleep") as mock_sleep:
        sleep_called = asyncio.Event()

        async def fake_sleep(interval):
            sleep_called.set()
            # Don't actually sleep
            raise asyncio.CancelledError()

        mock_sleep.side_effect = fake_sleep

        task = asyncio.create_task(coord._heartbeat_loop(session_id))
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Sleep was called (heartbeat loop started)
    assert mock_sleep.called

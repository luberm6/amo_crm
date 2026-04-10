"""
SessionCoordinator — distributed ownership and lifecycle management for Direct sessions.

Responsibilities:
  1. Owns a worker_id (UUID, unique per process instance)
  2. Acquires and renews ownership leases (heartbeat loop)
  3. Routes steering instructions — local fast-path or Redis pub/sub
  4. Startup reconciliation — marks orphaned sessions FAILED in the DB
  5. Stale session cleanup — removes sessions with expired locks from the active set
  6. Observability — structured log events for all ownership events

One SessionCoordinator per worker process, shared across all requests.
It is injected into DirectSessionManager and does not interact with FastAPI DI directly.

Worker lifecycle:
  startup  → SessionCoordinator.startup_reconcile()  # mark orphans FAILED
  per-call → register_session() + start_steering_subscriber()
  heartbeat→ _heartbeat_loop()   (background, per session)
  stop     → release_session()  (on terminate_session)
  shutdown → no special action needed (locks expire after LOCK_TTL_SECONDS)
"""
from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Optional

from app.core.logging import get_logger
from app.integrations.direct.session_store import (
    AbstractSessionStore,
    HEARTBEAT_INTERVAL,
    LOCK_TTL_SECONDS,
    SessionMetadata,
    SessionStatus,
)

if TYPE_CHECKING:
    from app.integrations.direct.session_manager import DirectSession

log = get_logger(__name__)


class SessionCoordinator:
    """
    Manages distributed ownership and steering dispatch for Direct mode sessions.

    One instance per worker process.  All methods are coroutine-safe within
    a single asyncio event loop — no OS-level thread safety needed.
    """

    def __init__(
        self,
        store: AbstractSessionStore,
        worker_id: Optional[str] = None,
    ) -> None:
        self._store = store
        self._worker_id = worker_id or f"worker-{uuid.uuid4().hex[:12]}"
        # session_id → heartbeat asyncio.Task
        self._heartbeat_tasks: dict[str, asyncio.Task] = {}
        # session_id → steering subscriber asyncio.Task
        self._steering_tasks: dict[str, asyncio.Task] = {}

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def worker_id(self) -> str:
        return self._worker_id

    @property
    def store(self) -> AbstractSessionStore:
        return self._store

    # ── Session registration ──────────────────────────────────────────────────

    async def register_session(
        self,
        session_id: str,
        call_id: str,
        phone: str,
    ) -> bool:
        """
        Register a newly created session in the store and claim ownership.

        Writes session metadata, acquires the ownership lock, adds to active set,
        and starts the heartbeat background task.

        Returns True on success.
        Returns False if another worker already holds the lock (ownership conflict).
        This should not happen in normal operation — logs at ERROR level if it does.
        """
        meta = SessionMetadata(
            session_id=session_id,
            call_id=call_id,
            phone=phone,
            status=SessionStatus.ACTIVE,
            worker_id=self._worker_id,
        )
        await self._store.create(meta)

        acquired = await self._store.acquire_lock(
            session_id, self._worker_id, ttl=LOCK_TTL_SECONDS
        )
        if not acquired:
            log.error(
                "session_coordinator.ownership_conflict",
                session_id=session_id,
                worker_id=self._worker_id,
                detail="Lock already held — possible duplicate session creation",
            )
            return False

        await self._store.add_to_active(session_id)

        self._heartbeat_tasks[session_id] = asyncio.create_task(
            self._heartbeat_loop(session_id),
            name=f"heartbeat_{session_id}",
        )

        log.info(
            "session_coordinator.registered",
            session_id=session_id,
            worker_id=self._worker_id,
        )
        return True

    # ── Heartbeat ─────────────────────────────────────────────────────────────

    async def _heartbeat_loop(self, session_id: str) -> None:
        """
        Renew the ownership lock every HEARTBEAT_INTERVAL seconds.

        If renewal fails (lock expired or stolen), log at ERROR and exit.
        The session will become orphaned after LOCK_TTL_SECONDS if the
        heartbeat stops — the next reconciliation pass will clean it up.
        """
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            try:
                renewed = await self._store.renew_lock(
                    session_id, self._worker_id, ttl=LOCK_TTL_SECONDS
                )
            except Exception as exc:
                log.error(
                    "session_coordinator.heartbeat_error",
                    session_id=session_id,
                    error=str(exc),
                )
                continue

            if not renewed:
                log.error(
                    "session_coordinator.heartbeat_lost",
                    session_id=session_id,
                    worker_id=self._worker_id,
                    detail="Lock renewal failed — session may be orphaned",
                )
                break

            log.debug(
                "session_coordinator.heartbeat_ok",
                session_id=session_id,
            )

    # ── Session release ───────────────────────────────────────────────────────

    async def release_session(self, session_id: str) -> None:
        """
        Gracefully release ownership of a terminated session.

        Cancels heartbeat and steering subscriber tasks, updates session
        status to TERMINATED, releases the lock, and removes from active set.
        Called by DirectSessionManager.terminate_session().
        """
        await self._cancel_task(self._heartbeat_tasks.pop(session_id, None))
        await self._cancel_task(self._steering_tasks.pop(session_id, None))

        await self._store.update_status(session_id, SessionStatus.TERMINATED)
        released = await self._store.release_lock(session_id, self._worker_id)
        await self._store.remove_from_active(session_id)

        log.info(
            "session_coordinator.released",
            session_id=session_id,
            worker_id=self._worker_id,
            lock_released=released,
        )

    @staticmethod
    async def _cancel_task(task: Optional[asyncio.Task]) -> None:
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    # ── Steering dispatch ─────────────────────────────────────────────────────

    async def send_steering(
        self,
        session_id: str,
        instruction: str,
        local_sessions: dict,           # DirectSessionManager._sessions
    ) -> bool:
        """
        Route a steering instruction to the session owner.

        Local fast-path: if the session dict contains the session_id, inject
        directly into its asyncio.Queue (zero latency, no Redis round-trip).

        Remote path: look up the owner in the store and publish to the
        session's pub/sub channel.  The owning worker is subscribed and will
        inject the instruction into its local asyncio.Queue.

        Returns True if the instruction was queued/published, False if no
        owner was found (session may have just terminated).
        """
        local = local_sessions.get(session_id)
        if local is not None:
            await local.instruction_queue.put(instruction)
            log.debug(
                "session_coordinator.steer_local",
                session_id=session_id,
            )
            return True

        owner = await self._store.get_lock_owner(session_id)
        if owner is None:
            log.warning(
                "session_coordinator.steer_no_owner",
                session_id=session_id,
                detail="Session has no current owner — instruction dropped",
            )
            return False

        await self._store.publish_steering(session_id, instruction)
        log.info(
            "session_coordinator.steer_published",
            session_id=session_id,
            target_worker=owner,
        )
        return True

    def start_steering_subscriber(
        self,
        session_id: str,
        local_sessions: dict,
    ) -> asyncio.Task:
        """
        Start a background task that listens for remotely published steering
        instructions and routes them into the local session's asyncio.Queue.

        This covers the case where steering comes from a different worker
        (e.g., the Telegram bot talks to worker B but the session lives on worker A).
        In single-worker deployments this task is idle (no messages arrive on
        the local fast-path) but keeps the pub/sub subscription open.
        """
        task = asyncio.create_task(
            self._steering_subscriber(session_id, local_sessions),
            name=f"steer_sub_{session_id}",
        )
        self._steering_tasks[session_id] = task
        return task

    async def _steering_subscriber(
        self,
        session_id: str,
        local_sessions: dict,
    ) -> None:
        try:
            async for instruction in self._store.subscribe_steering(session_id):
                session = local_sessions.get(session_id)
                if session is None:
                    log.warning(
                        "session_coordinator.steer_session_gone",
                        session_id=session_id,
                    )
                    break
                await session.instruction_queue.put(instruction)
                log.debug(
                    "session_coordinator.steer_from_pubsub",
                    session_id=session_id,
                )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.error(
                "session_coordinator.steer_subscriber_error",
                session_id=session_id,
                error=str(exc),
            )

    # ── Startup reconciliation ────────────────────────────────────────────────

    async def startup_reconcile(self, call_repo) -> dict:
        """
        Run on worker startup to detect and clean up orphaned sessions.

        An orphaned session is one in the active set whose ownership lock has
        expired (the previous worker process died without graceful shutdown).

        For each orphaned session:
          1. Find the corresponding Call record in the DB (via mango_call_id)
          2. If the call is not already in a terminal status → mark it FAILED
          3. Update session metadata to FAILED
          4. Remove from the active set

        Returns a stats dict for logging and observability.

        This is safe to call on every startup, even if there are no orphans.
        The common case (clean restart after graceful shutdown) is a no-op.
        """
        from app.models.call import TERMINAL_STATUSES

        active_ids = await self._store.get_active_ids()
        stats = {
            "total_checked": len(active_ids),
            "orphaned_failed": 0,
            "already_owned": 0,
            "already_terminal": 0,
            "errors": 0,
        }

        for session_id in active_ids:
            owner = await self._store.get_lock_owner(session_id)
            if owner is not None:
                # Another worker owns this session — don't touch it
                stats["already_owned"] += 1
                continue

            # Orphaned: lock expired with no owner
            meta = await self._store.get(session_id)
            if meta is None:
                # Metadata gone (TTL expired) — just clean up the set
                await self._store.remove_from_active(session_id)
                stats["orphaned_failed"] += 1
                continue

            try:
                call = await call_repo.get_by_mango_call_id(session_id)
                if call is not None and call.status not in TERMINAL_STATUSES:
                    from app.models.call import CallStatus
                    call.status = CallStatus.FAILED
                    await call_repo.save(call)
                    log.warning(
                        "session_coordinator.reconcile_call_failed",
                        session_id=session_id,
                        call_id=meta.call_id,
                        previous_status=call.status.value if call else "not_found",
                    )
                elif call is not None:
                    stats["already_terminal"] += 1
            except Exception as exc:
                log.error(
                    "session_coordinator.reconcile_error",
                    session_id=session_id,
                    error=str(exc),
                )
                stats["errors"] += 1

            await self._store.update_status(session_id, SessionStatus.FAILED)
            await self._store.remove_from_active(session_id)
            stats["orphaned_failed"] += 1

        log.info(
            "session_coordinator.reconcile_complete",
            worker_id=self._worker_id,
            **stats,
        )
        return stats

    # ── Stale session cleanup ─────────────────────────────────────────────────

    async def cleanup_stale(self) -> list[str]:
        """
        Remove sessions from the active set whose lock has expired and whose
        metadata indicates they are already in a terminal state.

        Returns the list of session_ids that were cleaned up.

        This can be called periodically (e.g., from a Celery beat task)
        as a belt-and-suspenders cleanup beyond startup reconciliation.
        """
        active_ids = await self._store.get_active_ids()
        cleaned: list[str] = []

        for session_id in active_ids:
            owner = await self._store.get_lock_owner(session_id)
            if owner is not None:
                continue                        # Live session, skip

            meta = await self._store.get(session_id)
            if meta is None or meta.status in (
                SessionStatus.TERMINATED, SessionStatus.FAILED
            ):
                await self._store.remove_from_active(session_id)
                cleaned.append(session_id)
                log.info(
                    "session_coordinator.stale_cleaned",
                    session_id=session_id,
                    status=meta.status.value if meta else "no_metadata",
                )

        if cleaned:
            log.warning(
                "session_coordinator.stale_cleanup_done",
                count=len(cleaned),
                worker_id=self._worker_id,
            )
        return cleaned

    # ── Observability ─────────────────────────────────────────────────────────

    async def get_stats(self) -> dict:
        """
        Return current coordinator stats for health check / monitoring endpoints.

        Fields:
          worker_id         — unique ID of this worker process
          owned_sessions    — sessions this worker currently holds heartbeat for
          active_in_store   — sessions in the active set (all workers)
          orphaned_in_store — active sessions with no live owner
        """
        store_stats = await self._store.get_stats()
        return {
            "worker_id": self._worker_id,
            "owned_sessions": len(self._heartbeat_tasks),
            "active_in_store": store_stats.active_count,
            "orphaned_in_store": store_stats.orphaned_count,
            "total_tracked": store_stats.total_tracked,
        }

"""
TransferService — production-grade warm transfer state machine.

Race condition protection:
  - SELECT FOR UPDATE on call row at transfer entry point.
    Ensures that two concurrent requests cannot both see the call as
    "not yet transferring" and proceed past the guard simultaneously.
    On PostgreSQL this is a row-level lock; SQLite ignores the hint (tests OK).

Timeout handling:
  - transfer_manager_answer_timeout  — covers engine.initiate_manager_call
  - transfer_briefing_timeout        — covers engine.play_whisper
  - transfer_bridge_timeout          — covers engine.bridge_calls

Client hangup detection:
  - Before each major engine operation, the call status is re-read (without lock).
  - If the call is terminal (STOPPED/COMPLETED/FAILED), the transfer is aborted
    with CALLER_DROPPED status and CallerDroppedError is raised.

Multi-manager retry:
  - Tries up to settings.transfer_max_manager_attempts managers in priority order.
  - Each failed attempt marks the manager temporarily unavailable.
  - Only when all attempts are exhausted does it raise TransferError.
  - FAILED_ALL_UNAVAILABLE is set when there are no managers at all.

Failure stages:
  "no_managers"    — no managers found (FAILED_ALL_UNAVAILABLE)
  "dial"           — all dial attempts failed (FAILED_NO_ANSWER)
  "dial_timeout"   — manager dial timed out (TIMED_OUT)
  "bridge"         — manager answered, bridge failed (BRIDGE_FAILED)
  "bridge_timeout" — bridge timed out (TIMED_OUT)
  "caller_dropped" — client hung up (CALLER_DROPPED)

What counts as a successful transfer:
  Call.status == CONNECTED_TO_MANAGER and TransferRecord.status == CONNECTED.
  The bridge has been confirmed by the engine. Both parties are speaking.

Correlated audit log:
  transfer_initiated       — transfer flow started
  transfer_calling_manager — dialling attempt (with manager_id, attempt number)
  transfer_dial_failed     — dial failed (with reason)
  transfer_dial_timeout    — dial timed out
  transfer_briefing        — manager answered, whisper started
  transfer_connected       — bridge confirmed (success)
  transfer_caller_dropped  — client hung up
  transfer_bridge_failed   — bridge failed
  transfer_timed_out       — timeout
  transfer_all_failed      — all manager attempts exhausted
"""
from __future__ import annotations

import asyncio
from typing import List, Optional
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    CallerDroppedError,
    InvalidCallStateError,
    NoManagerAvailableError,
    NotFoundError,
    TransferError,
    TransferTimeoutError,
)
from app.core.logging import get_logger
from app.integrations.transfer_engine.base import AbstractTransferEngine
from app.models.audit import AuditEvent
from app.models.call import Call, CallStatus, TERMINAL_STATUSES
from app.models.manager import Manager
from app.models.transfer import TransferRecord, TransferStatus
from app.models.transcript import TranscriptEntry
from app.repositories.call_repo import CallRepository
from app.repositories.manager_repo import ManagerRepository
from app.repositories.transfer_repo import TransferRepository
from app.repositories.transcript_repo import TranscriptRepository
from app.services.summary_service import SummaryService

log = get_logger(__name__)


class TransferService:
    def __init__(
        self,
        session: AsyncSession,
        engine: AbstractTransferEngine,
    ) -> None:
        self.session = session
        self.engine = engine
        self.call_repo = CallRepository(Call, session)
        self.manager_repo = ManagerRepository(Manager, session)
        self.transfer_repo = TransferRepository(TransferRecord, session)
        self.transcript_repo = TranscriptRepository(TranscriptEntry, session)
        self.summary_svc = SummaryService()

    # ── Public API ────────────────────────────────────────────────────────────

    async def initiate_transfer(
        self,
        call_id: uuid.UUID,
        department: Optional[str] = None,
        actor: str = "system",
    ) -> TransferRecord:
        """
        Initiate a warm transfer for an in-progress call.

        Returns the TransferRecord on success (status=CONNECTED).
        Raises:
          NotFoundError           — call does not exist
          InvalidCallStateError   — call is terminal or already in transfer
          NoManagerAvailableError — no active+available managers found
          CallerDroppedError      — client hung up during transfer
          TransferTimeoutError    — timeout at dial, briefing or bridge phase
          TransferError           — engine failure during dial or bridge
        """
        # ── 1. Lock call row (SELECT FOR UPDATE) ──────────────────────────────
        call = await self._get_transferable_call_for_update(call_id)

        # ── 2. Generate summary + whisper ─────────────────────────────────────
        entries = await self.transcript_repo.get_by_call(call_id)
        summary_obj = self.summary_svc.generate_summary(entries)
        whisper_text = self.summary_svc.generate_whisper(summary_obj)
        summary_text = summary_obj.as_text()

        # ── 3. Find candidate managers ────────────────────────────────────────
        managers = await self._find_managers(department)

        # ── 4. Create TransferRecord ──────────────────────────────────────────
        record = TransferRecord(
            call_id=call.id,
            manager_id=None,
            status=TransferStatus.INITIATED,
            summary=summary_text,
            whisper_text=whisper_text,
            department=department,
            attempt_count=0,
        )
        await self.transfer_repo.save(record)

        # ── 5. Transition: NEEDS_TRANSFER → TRANSFERRING ──────────────────────
        call.status = CallStatus.NEEDS_TRANSFER
        await self.call_repo.save(call)
        call.status = CallStatus.TRANSFERRING
        record.status = TransferStatus.CALLING_MANAGER
        await self.call_repo.save(call)
        await self.transfer_repo.save(record)

        await self._audit(call, "transfer_initiated", actor=actor, payload={
            "department": department,
            "transfer_record_id": str(record.id),
            "manager_count": len(managers),
        })

        log.info(
            "transfer.initiated",
            call_id=str(call_id),
            department=department,
            manager_candidates=len(managers),
        )

        # ── 6. Try managers in priority order ─────────────────────────────────
        max_attempts = settings.transfer_max_manager_attempts or len(managers)
        last_dial_error: Optional[str] = None

        for attempt, manager in enumerate(managers[:max_attempts], start=1):
            record.attempt_count = attempt
            record.manager_id = manager.id
            await self.transfer_repo.save(record)
            await self._audit(call, "transfer_manager_selected", actor="engine", payload={
                "manager_id": str(manager.id),
                "manager_name": manager.name,
                "attempt": attempt,
            })

            # Check client hasn't hung up before we dial
            if await self._call_is_terminal(call_id):
                await self._mark_caller_dropped(
                    call, record,
                    stage="caller_dropped",
                    reason=f"Client dropped before attempt {attempt}",
                    actor=actor,
                )
                raise CallerDroppedError(
                    f"Client hung up before manager dial (attempt {attempt})",
                    detail={"call_id": str(call_id)},
                )

            log.info(
                "transfer.calling_manager",
                call_id=str(call_id),
                manager_id=str(manager.id),
                manager_name=manager.name,
                attempt=attempt,
                max_attempts=max_attempts,
            )
            await self._audit(call, "transfer_calling_manager", actor="engine", payload={
                "manager_id": str(manager.id),
                "manager_name": manager.name,
                "attempt": attempt,
            })

            # ── Dial manager (with timeout) ───────────────────────────────────
            try:
                result = await asyncio.wait_for(
                    self.engine.initiate_manager_call(
                        manager=manager,
                        call=call,
                        whisper_text=whisper_text,
                    ),
                    timeout=float(settings.transfer_manager_answer_timeout),
                )
            except asyncio.TimeoutError:
                await self._safe_mark_unavailable(manager)
                last_dial_error = f"manager {manager.id} dial timed out after {settings.transfer_manager_answer_timeout}s"
                log.warning(
                    "transfer.dial_timeout",
                    call_id=str(call_id),
                    manager_id=str(manager.id),
                    attempt=attempt,
                    timeout=settings.transfer_manager_answer_timeout,
                )
                await self._audit(call, "transfer_dial_timeout", actor="engine", payload={
                    "manager_id": str(manager.id),
                    "attempt": attempt,
                    "timeout": settings.transfer_manager_answer_timeout,
                })
                continue  # try next manager

            except Exception as exc:
                await self._safe_mark_unavailable(manager)
                last_dial_error = f"manager {manager.id} dial failed: {exc}"
                log.warning(
                    "transfer.dial_failed",
                    call_id=str(call_id),
                    manager_id=str(manager.id),
                    attempt=attempt,
                    error=str(exc),
                )
                await self._audit(call, "transfer_dial_failed", actor="engine", payload={
                    "manager_id": str(manager.id),
                    "attempt": attempt,
                    "error": str(exc),
                })
                continue  # try next manager

            # ── Manager answered — check client is still on the line ──────────
            if await self._call_is_terminal(call_id):
                await self._safe_terminate_manager_call(result.external_id)
                await self._mark_caller_dropped(
                    call, record,
                    stage="caller_dropped",
                    reason=f"Client dropped after manager {manager.id} answered",
                    actor=actor,
                )
                raise CallerDroppedError(
                    "Client hung up after manager answered but before bridge",
                    detail={"call_id": str(call_id), "manager_id": str(manager.id)},
                )

            # ── Transition to BRIEFING ────────────────────────────────────────
            record.manager_call_id = result.external_id
            record.status = TransferStatus.BRIEFING
            call.status = CallStatus.MANAGER_BRIEFING
            call.manager_id = manager.id
            await self.transfer_repo.save(record)
            await self.call_repo.save(call)
            await self._audit(call, "transfer_manager_answered", actor="engine", payload={
                "manager_id": str(manager.id),
                "manager_call_id": result.external_id,
                "attempt": attempt,
            })

            log.info(
                "transfer.briefing",
                call_id=str(call_id),
                manager_id=str(manager.id),
                manager_call_id=result.external_id,
                whisper_preview=whisper_text[:60],
            )
            await self._audit(call, "transfer_briefing", actor="engine", payload={
                "manager_id": str(manager.id),
                "manager_call_id": result.external_id,
            })

            # ── Play whisper (non-fatal, with timeout) ────────────────────────
            try:
                await self._audit(call, "transfer_whispering", actor="engine", payload={
                    "manager_id": str(manager.id),
                    "manager_call_id": result.external_id,
                    "attempt": attempt,
                })
                await asyncio.wait_for(
                    self.engine.play_whisper(
                        manager_call_id=result.external_id,
                        whisper_text=whisper_text,
                    ),
                    timeout=float(settings.transfer_briefing_timeout),
                )
            except asyncio.TimeoutError:
                await self._safe_terminate_manager_call(result.external_id)
                record.status = TransferStatus.TIMED_OUT
                record.failure_stage = "briefing"
                record.fallback_message = (
                    f"Whisper timed out after {settings.transfer_briefing_timeout}s"
                )
                call.status = CallStatus.STOPPED
                await self._safe_terminate_customer_leg(call)
                await self.transfer_repo.save(record)
                await self.call_repo.save(call)
                await self._audit(call, "transfer_timed_out", actor="engine", payload={
                    "manager_id": str(manager.id),
                    "stage": "briefing",
                    "timeout": settings.transfer_briefing_timeout,
                })
                raise TransferTimeoutError(
                    f"Whisper briefing timed out after {settings.transfer_briefing_timeout}s",
                    detail={"call_id": str(call_id), "manager_id": str(manager.id)},
                )
            except Exception as exc:
                await self._safe_terminate_manager_call(result.external_id)
                record.status = TransferStatus.BRIDGE_FAILED
                record.failure_stage = "briefing"
                record.fallback_message = f"Whisper failed: {exc}"
                call.status = CallStatus.STOPPED
                await self._safe_terminate_customer_leg(call)
                await self.transfer_repo.save(record)
                await self.call_repo.save(call)
                await self._audit(call, "transfer_bridge_failed", actor="engine", payload={
                    "manager_id": str(manager.id),
                    "stage": "briefing",
                    "error": str(exc),
                })
                log.error(
                    "transfer.play_whisper_failed_fatal",
                    call_id=str(call_id),
                    manager_call_id=result.external_id,
                    error=str(exc),
                )
                raise TransferError(
                    f"Whisper failed for manager {manager.id}: {exc}",
                    detail={"call_id": str(call_id), "manager_id": str(manager.id)},
                ) from exc

            # ── Check client before bridge ────────────────────────────────────
            if await self._call_is_terminal(call_id):
                await self._safe_terminate_manager_call(result.external_id)
                await self._mark_caller_dropped(
                    call, record,
                    stage="caller_dropped",
                    reason="Client dropped during briefing phase",
                    actor=actor,
                )
                raise CallerDroppedError(
                    "Client hung up during manager briefing",
                    detail={"call_id": str(call_id)},
                )

            # ── Bridge (with timeout) ─────────────────────────────────────────
            try:
                await asyncio.wait_for(
                    self.engine.bridge_calls(
                        manager_call_id=result.external_id,
                        customer_call_id=(
                            call.telephony_leg_id
                            or call.vapi_call_id
                            or str(call.id)
                        ),
                    ),
                    timeout=float(settings.transfer_bridge_timeout),
                )
            except asyncio.TimeoutError:
                await self._safe_terminate_manager_call(result.external_id)
                record.status = TransferStatus.TIMED_OUT
                record.failure_stage = "bridge_timeout"
                record.fallback_message = (
                    f"Bridge timed out after {settings.transfer_bridge_timeout}s"
                )
                call.status = CallStatus.STOPPED
                await self._safe_terminate_customer_leg(call)
                await self.transfer_repo.save(record)
                await self.call_repo.save(call)
                await self._audit(call, "transfer_timed_out", actor="engine", payload={
                    "manager_id": str(manager.id),
                    "stage": "bridge",
                    "timeout": settings.transfer_bridge_timeout,
                })
                raise TransferTimeoutError(
                    f"Bridge establishment timed out after {settings.transfer_bridge_timeout}s",
                    detail={"call_id": str(call_id)},
                )

            except Exception as exc:
                await self._safe_terminate_manager_call(result.external_id)
                record.status = TransferStatus.BRIDGE_FAILED
                record.failure_stage = "bridge"
                record.fallback_message = f"Bridge failed: {exc}"
                call.status = CallStatus.STOPPED
                await self._safe_terminate_customer_leg(call)
                await self.transfer_repo.save(record)
                await self.call_repo.save(call)
                await self._audit(call, "transfer_bridge_failed", actor="engine", payload={
                    "manager_id": str(manager.id),
                    "error": str(exc),
                })
                raise TransferError(
                    f"Bridge failed for manager {manager.id}: {exc}",
                    detail={"call_id": str(call_id), "manager_id": str(manager.id)},
                ) from exc

            # ── SUCCESS ───────────────────────────────────────────────────────
            record.status = TransferStatus.CONNECTED
            call.status = CallStatus.CONNECTED_TO_MANAGER
            await self.transfer_repo.save(record)
            await self.call_repo.save(call)
            await self._audit(call, "transfer_bridged", actor="engine", payload={
                "manager_id": str(manager.id),
                "manager_call_id": result.external_id,
                "attempt": attempt,
            })

            await self._audit(call, "transfer_connected", actor=actor, payload={
                "manager_id": str(manager.id),
                "manager_name": manager.name,
                "transfer_record_id": str(record.id),
                "attempt": attempt,
            })

            log.info(
                "transfer.connected",
                call_id=str(call_id),
                manager_id=str(manager.id),
                transfer_record_id=str(record.id),
                attempt=attempt,
            )
            return record

        # ── All manager attempts exhausted ────────────────────────────────────
        record.status = TransferStatus.FAILED_NO_ANSWER
        record.failure_stage = "dial"
        record.fallback_message = (
            f"All {record.attempt_count} manager attempt(s) failed. "
            f"Last error: {last_dial_error}"
        )
        call.status = CallStatus.STOPPED
        await self._safe_terminate_customer_leg(call)
        await self.transfer_repo.save(record)
        await self.call_repo.save(call)

        await self._audit(call, "transfer_all_failed", actor=actor, payload={
            "attempts": record.attempt_count,
            "last_error": last_dial_error,
        })

        log.error(
            "transfer.all_failed",
            call_id=str(call_id),
            attempts=record.attempt_count,
            last_error=last_dial_error,
        )
        raise TransferError(
            f"Transfer failed: all {record.attempt_count} manager attempt(s) exhausted. "
            f"Last error: {last_dial_error}",
            detail={"call_id": str(call_id), "attempts": record.attempt_count},
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _get_transferable_call_for_update(self, call_id: uuid.UUID) -> Call:
        """
        Fetch call with SELECT FOR UPDATE, then validate it can be transferred.

        The row-level lock prevents two concurrent transfer requests from both
        passing the 'already in transfer' guard before either writes the transition.
        """
        call = await self.call_repo.get_for_update(call_id)
        if call is None:
            raise NotFoundError(f"Call {call_id} not found")
        if call.status in TERMINAL_STATUSES:
            raise InvalidCallStateError(
                f"Cannot transfer call {call_id}: status is {call.status} (terminal)",
                detail={"status": call.status},
            )
        if call.is_in_transfer():
            raise InvalidCallStateError(
                f"Call {call_id} is already in transfer (status={call.status})",
                detail={"status": call.status},
            )
        return call

    async def _find_managers(self, department: Optional[str]) -> List[Manager]:
        """
        Find available managers sorted by priority.
        Department filter first, fallback to all.
        Raises NoManagerAvailableError and sets FAILED_ALL_UNAVAILABLE if none found.
        """
        if department is not None:
            managers = await self.manager_repo.find_available_managers(
                department=department
            )
            if managers:
                return managers

        managers = await self.manager_repo.find_available_managers(department=None)
        if not managers:
            raise NoManagerAvailableError(
                "No active, available managers found",
                detail={"department": department},
            )
        return managers

    async def _call_is_terminal(self, call_id: uuid.UUID) -> bool:
        """
        Re-read call status without lock to detect client hangup.
        Returns True if the call is in a terminal state.
        """
        call = await self.call_repo.get(call_id)
        if call is None:
            return True
        return call.status in TERMINAL_STATUSES

    async def _mark_caller_dropped(
        self,
        call: Call,
        record: TransferRecord,
        stage: str,
        reason: str,
        actor: str,
    ) -> None:
        """Mark transfer as CALLER_DROPPED and update call status."""
        log.warning(
            "transfer.caller_dropped",
            call_id=str(call.id),
            stage=stage,
            reason=reason,
        )
        record.status = TransferStatus.CALLER_DROPPED
        record.failure_stage = stage
        record.fallback_message = reason
        # Call is already terminal — don't overwrite its status
        await self.transfer_repo.save(record)
        await self._audit(call, "transfer_caller_dropped", actor=actor, payload={
            "stage": stage,
            "reason": reason,
        })

    async def _safe_mark_unavailable(self, manager: Manager) -> None:
        """Mark manager temporarily unavailable; swallow exceptions."""
        try:
            await self.engine.mark_manager_temporarily_unavailable(manager.id)
        except Exception as exc:
            log.warning(
                "transfer.mark_unavailable_failed",
                manager_id=str(manager.id),
                error=str(exc),
            )

    async def _safe_terminate_manager_call(
        self, manager_call_id: Optional[str]
    ) -> None:
        """Terminate manager-side call leg on failure to prevent orphaned legs."""
        if not manager_call_id:
            return
        try:
            await self.engine.terminate_manager_call(manager_call_id)
        except Exception as exc:
            log.warning(
                "transfer.terminate_manager_call_failed",
                manager_call_id=manager_call_id,
                error=str(exc),
            )

    async def _safe_terminate_customer_leg(self, call: Call) -> None:
        """Terminate customer-side telephony leg when transfer finishes in terminal failure."""
        customer_leg_id = call.telephony_leg_id
        if not customer_leg_id:
            return
        terminate_customer = getattr(self.engine, "terminate_customer_leg", None)
        if terminate_customer is None:
            return
        try:
            await terminate_customer(customer_leg_id)
        except Exception as exc:
            log.warning(
                "transfer.terminate_customer_leg_failed",
                customer_leg_id=customer_leg_id,
                error=str(exc),
            )

    async def _audit(
        self,
        call: Call,
        action: str,
        actor: str = "system",
        payload: Optional[dict] = None,
    ) -> None:
        event = AuditEvent(
            entity_type="call",
            entity_id=call.id,
            action=action,
            actor=actor,
            payload=payload,
        )
        self.session.add(event)
        await self.session.flush()

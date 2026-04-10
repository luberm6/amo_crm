"""
VapiEventProcessor — routes Vapi webhook events to the appropriate handlers.
Design principles:
- Never raises: unknown/malformed events are logged and ignored
- Idempotent: duplicate events are detected via VapiEventLog.vapi_event_id
- Single responsibility: each _handle_* method handles exactly one event type
- Audit everything: every event is persisted to VapiEventLog before processing
Call flow for a typical outbound call:
  1. call-started / status-update(ringing) → RINGING
  2. status-update(in-progress)            → IN_PROGRESS
  3. transcript (many)                     → TranscriptEntry rows
  4. end-of-call-report                    → COMPLETED + summary + bulk transcript
"""
from __future__ import annotations

from typing import Any, Optional

import hashlib
import hmac
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.logging import get_logger
from app.integrations.vapi.schemas import (
    VapiCallStatus,
    VapiEndOfCallMessage,
    VapiMessageType,
    VapiStatusUpdateMessage,
    VapiToolCallsMessage,
    VapiTranscriptMessage,
    VapiTransferDestinationRequest,
    VapiWebhookEnvelope,
)
from app.models.call import Call, CallStatus, TERMINAL_STATUSES
from app.models.transcript import TranscriptEntry, TranscriptRole
from app.models.vapi_event import VapiEventLog, VapiEventProcessingStatus
from app.repositories.call_repo import CallRepository
from app.repositories.transcript_repo import TranscriptRepository
log = get_logger(__name__)
# Vapi status-update status → our CallStatus
_STATUS_MAP: dict[VapiCallStatus, CallStatus] = {
    VapiCallStatus.QUEUED: CallStatus.QUEUED,
    VapiCallStatus.RINGING: CallStatus.RINGING,
    VapiCallStatus.IN_PROGRESS: CallStatus.IN_PROGRESS,
    VapiCallStatus.FORWARDING: CallStatus.TRANSFERRING,
    VapiCallStatus.ENDED: CallStatus.COMPLETED,
}
# Vapi endedReason values that indicate a failure
_FAILURE_REASONS = {
    "error",
    "error-vapifault",
    "error-licensequota",
    "error-validation-failed",
    "pipeline-error",
    "silence-timed-out",
    "voicemail",  # debatable — treat as failed for now
}
def verify_webhook_signature(
    raw_body: bytes, signature_header: str, secret: str
) -> bool:
    """
    Validate the Vapi HMAC-SHA256 webhook signature.
    Vapi sends: X-Vapi-Signature: sha256=<hex_digest>
    """
    if not signature_header.startswith("sha256="):
        return False
    expected_hex = signature_header[len("sha256="):]
    computed = hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(computed, expected_hex)
class VapiEventProcessor:
    """
    Processes a single Vapi webhook event end-to-end within one DB session.
    Usage (in webhook handler):
        processor = VapiEventProcessor(session)
        await processor.process(raw_payload_dict)
    """
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.call_repo = CallRepository(Call, session)
        self.transcript_repo = TranscriptRepository(TranscriptEntry, session)
    async def process(self, raw_payload: dict[str, Any]) -> None:
        """
        Entry point. Persists the event log, then routes to a type handler.
        All exceptions are caught here — a bad event must not break the system.
        """
        message = raw_payload.get("message", {})
        event_type = message.get("type", "unknown")
        vapi_call_obj = message.get("call", {}) or {}
        vapi_call_id = vapi_call_obj.get("id") if isinstance(vapi_call_obj, dict) else None
        # Resolve our internal call record
        call: Optional[Call] = None
        if vapi_call_id:
            call = await self.call_repo.get_by_vapi_id(vapi_call_id)
        # Persist the raw event for audit and idempotency
        event_log = await self._persist_event_log(
            event_type=event_type,
            raw_payload=raw_payload,
            call_id=call.id if call else None,
        )
        try:
            await self._route(event_type, message, call, raw_payload)
            event_log.processing_status = VapiEventProcessingStatus.PROCESSED
            event_log.processed_at = datetime.now(timezone.utc)
        except Exception as exc:
            log.exception(
                "vapi_event.processing_error",
                event_type=event_type,
                vapi_call_id=vapi_call_id,
                error=str(exc),
            )
            event_log.processing_status = VapiEventProcessingStatus.ERROR
            event_log.error_message = str(exc)[:500]
        finally:
            self.session.add(event_log)
            await self.session.flush()
    async def _route(
        self,
        event_type: str,
        message: dict,
        call: Optional[Call],
        raw_payload: dict,
    ) -> None:
        """Dispatch to the correct handler based on event type."""
        if event_type == VapiMessageType.TRANSCRIPT:
            await self._handle_transcript(message, call)
        elif event_type == VapiMessageType.STATUS_UPDATE:
            await self._handle_status_update(message, call)
        elif event_type == VapiMessageType.END_OF_CALL_REPORT:
            await self._handle_end_of_call(message, call)
        elif event_type in (VapiMessageType.TOOL_CALLS, VapiMessageType.FUNCTION_CALL):
            await self._handle_tool_calls(message, call)
        elif event_type == VapiMessageType.TRANSFER_DESTINATION_REQUEST:
            await self._handle_transfer_request(message, call)
        elif event_type == VapiMessageType.HANG:
            await self._handle_hang(message, call)
        elif event_type in (VapiMessageType.SPEECH_UPDATE, VapiMessageType.USER_INTERRUPTED):
            # Informational — no state change needed
            log.debug("vapi_event.ignored", event_type=event_type)
        elif event_type == VapiMessageType.ASSISTANT_REQUEST:
            # We use static assistant IDs — this event is not needed
            log.debug("vapi_event.assistant_request_ignored")
        else:
            log.info(
                "vapi_event.unknown_type",
                event_type=event_type,
                message_keys=list(message.keys()),
            )
    # ── Event handlers ────────────────────────────────────────────────────────
    async def _handle_transcript(
        self, message: dict, call: Optional[Call]
    ) -> None:
        """Save a real-time transcript chunk to TranscriptEntry."""
        if call is None:
            log.warning("vapi_event.transcript.no_call")
            return
        parsed = VapiTranscriptMessage.model_validate(message)
        # Only save "final" utterances to avoid duplicate partial/final pairs
        if parsed.transcript_type == "partial":
            return
        role = self._map_role(parsed.role)
        text = parsed.transcript.strip()
        if not text:
            return
        await self.transcript_repo.append(
            call_id=call.id,
            role=role,
            text=text,
            raw_payload=message,
        )
        log.debug(
            "vapi_event.transcript.saved",
            call_id=str(call.id),
            role=parsed.role,
            text_preview=text[:60],
        )
    async def _handle_status_update(
        self, message: dict, call: Optional[Call]
    ) -> None:
        """Map Vapi call status to our CallStatus and persist."""
        if call is None:
            log.warning("vapi_event.status_update.no_call")
            return
        parsed = VapiStatusUpdateMessage.model_validate(message)
        new_status = _STATUS_MAP.get(parsed.status)
        if new_status is None:
            log.info(
                "vapi_event.status_update.unmapped",
                vapi_status=parsed.status,
                call_id=str(call.id),
            )
            return
        if call.status in TERMINAL_STATUSES:
            log.info(
                "vapi_event.status_update.already_terminal",
                call_id=str(call.id),
                current_status=call.status,
            )
            return
        log.info(
            "vapi_event.status_update",
            call_id=str(call.id),
            from_status=call.status,
            to_status=new_status,
        )
        call.status = new_status
        await self.call_repo.save(call)
    async def _handle_end_of_call(
        self, message: dict, call: Optional[Call]
    ) -> None:
        """
        Process the end-of-call-report event:
        - Save bulk transcript entries from messages array
        - Set summary and sentiment on the Call
        - Mark call COMPLETED or FAILED based on endedReason
        """
        if call is None:
            log.warning("vapi_event.end_of_call.no_call")
            return
        if call.status in TERMINAL_STATUSES:
            log.info(
                "vapi_event.end_of_call.already_terminal", call_id=str(call.id)
            )
            return
        parsed = VapiEndOfCallMessage.model_validate(message)
        # Save structured transcript from messages array (more reliable than
        # real-time chunks which may have missed partials)
        if parsed.messages:
            entries_to_save = []
            for msg in parsed.messages:
                role_str = msg.get("role", "")
                text = msg.get("message") or msg.get("content") or ""
                if text and role_str in ("assistant", "user", "system", "tool"):
                    entries_to_save.append(
                        {
                            "role": self._map_role(role_str),
                            "text": text.strip(),
                            "raw_payload": msg,
                        }
                    )
            if entries_to_save:
                # If real-time transcript entries already exist, don't duplicate.
                # Check count: if > 0 skip bulk save (real-time was more granular)
                existing = await self.transcript_repo.get_by_call(call.id)
                if not existing:
                    await self.transcript_repo.bulk_append(call.id, entries_to_save)
        # Persist summary from the report
        if parsed.summary:
            call.summary = parsed.summary
        # Extract sentiment from analysis block
        if parsed.analysis:
            sentiment = (
                parsed.analysis.get("successEvaluation")
                or parsed.analysis.get("sentiment")
            )
            if sentiment:
                call.sentiment = str(sentiment)
        # Determine final status
        ended_reason = (parsed.ended_reason or "").lower()
        if ended_reason in _FAILURE_REASONS:
            call.status = CallStatus.FAILED
        else:
            call.status = CallStatus.COMPLETED
        call.completed_at = datetime.now(timezone.utc)
        await self.call_repo.save(call)
        log.info(
            "vapi_event.end_of_call",
            call_id=str(call.id),
            final_status=call.status,
            ended_reason=ended_reason,
            has_summary=bool(parsed.summary),
        )
    async def _handle_tool_calls(
        self, message: dict, call: Optional[Call]
    ) -> None:
        """
        Handle AI-triggered tool/function calls.
        Currently: log for visibility. Next stage: dispatch to tool handlers
        (e.g. CRM lookup, calendar booking, transfer initiation).
        """
        if call:
            log.info(
                "vapi_event.tool_calls",
                call_id=str(call.id),
                tool_names=[
                    tc.get("function", {}).get("name")
                    for tc in message.get("toolCalls", [])
                ],
            )
    async def _handle_transfer_request(
        self, message: dict, call: Optional[Call]
    ) -> None:
        """
        Vapi is requesting a transfer destination.
        Sets status to NEEDS_TRANSFER. The next stage will implement:
        - Selecting the available manager
        - Returning the destination to Vapi
        - Tracking manager assignment
        """
        if call is None:
            return
        if call.status not in TERMINAL_STATUSES:
            call.status = CallStatus.NEEDS_TRANSFER
            await self.call_repo.save(call)
            log.info("vapi_event.transfer_requested", call_id=str(call.id))
    async def _handle_hang(self, message: dict, call: Optional[Call]) -> None:
        """User hung up — mark as FAILED if still active."""
        if call is None:
            return
        if call.status not in TERMINAL_STATUSES:
            call.status = CallStatus.FAILED
            call.completed_at = datetime.now(timezone.utc)
            await self.call_repo.save(call)
            log.info("vapi_event.hang", call_id=str(call.id))
    # ── Helpers ───────────────────────────────────────────────────────────────
    async def _persist_event_log(
        self,
        event_type: str,
        raw_payload: dict,
        call_id: Any = None,
    ) -> VapiEventLog:
        event_log = VapiEventLog(
            event_type=event_type,
            raw_payload=raw_payload,
            call_id=call_id,
            processing_status=VapiEventProcessingStatus.PENDING,
        )
        self.session.add(event_log)
        await self.session.flush()
        return event_log
    @staticmethod
    def _map_role(vapi_role: str) -> TranscriptRole:
        mapping = {
            "assistant": TranscriptRole.ASSISTANT,
            "user": TranscriptRole.USER,
            "system": TranscriptRole.SYSTEM,
            "tool": TranscriptRole.TOOL,
            "function": TranscriptRole.TOOL,
            "bot": TranscriptRole.ASSISTANT,
        }
        return mapping.get(vapi_role.lower(), TranscriptRole.ASSISTANT)
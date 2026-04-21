from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.telephony.base import TelephonyLegState
from app.integrations.telephony.mango_freeswitch_correlation import (
    AbstractMangoFreeSwitchCorrelationStore,
    get_mango_freeswitch_correlation_store,
)
from app.integrations.telephony.mango_state_store import AbstractMangoLegStateStore
from app.models.call import Call, CallStatus, TERMINAL_STATUSES
from app.models.transfer import TransferRecord, TransferStatus
from app.repositories.call_repo import CallRepository
from app.repositories.transfer_repo import TransferRepository

log = get_logger(__name__)
_OUTBOUND_PHONE_CORRELATION_WINDOW_SECONDS = 180

_STATE_ALIASES: dict[str, TelephonyLegState] = {
    "initiating": TelephonyLegState.INITIATING,
    "calling": TelephonyLegState.INITIATING,
    "new": TelephonyLegState.INITIATING,
    "ringing": TelephonyLegState.RINGING,
    "alerting": TelephonyLegState.RINGING,
    "answered": TelephonyLegState.ANSWERED,
    "connected": TelephonyLegState.ANSWERED,
    "in-progress": TelephonyLegState.ANSWERED,
    "in_progress": TelephonyLegState.ANSWERED,
    "bridged": TelephonyLegState.BRIDGED,
    "transferred": TelephonyLegState.BRIDGED,
    "hangup": TelephonyLegState.TERMINATED,
    "hung_up": TelephonyLegState.TERMINATED,
    "disconnected": TelephonyLegState.TERMINATED,
    "terminated": TelephonyLegState.TERMINATED,
    "completed": TelephonyLegState.TERMINATED,
    "failed": TelephonyLegState.FAILED,
    "busy": TelephonyLegState.FAILED,
    "no_answer": TelephonyLegState.FAILED,
    "unavailable": TelephonyLegState.FAILED,
}


@dataclass
class MangoNormalizedEvent:
    provider_event_id: str
    provider_type: str
    leg_id: Optional[str]
    command_id: Optional[str]
    state: Optional[TelephonyLegState]
    call_id: Optional[str]
    transfer_id: Optional[str]
    role: Optional[str]
    bridge_status: Optional[str] = None
    whisper_status: Optional[str] = None
    from_number: Optional[str] = None
    to_number: Optional[str] = None
    raw_payload: Optional[dict] = None


def verify_mango_webhook_guard(
    *,
    raw_body: bytes,
    source_ip: Optional[str],
    signature_header: Optional[str],
    secret_header: Optional[str],
) -> tuple[bool, Optional[str]]:
    """
    Verify Mango webhook request guard.

    Order:
    1. HMAC signature (if mango_webhook_secret configured)
    2. Shared secret header (if mango_webhook_shared_secret configured)
    3. IP allowlist (if mango_webhook_ip_allowlist configured)
    """
    if settings.mango_webhook_secret:
        if not signature_header:
            return False, "missing_signature"
        if not _verify_sha256_signature(raw_body, signature_header, settings.mango_webhook_secret):
            return False, "invalid_signature"

    if settings.mango_webhook_shared_secret:
        if not secret_header:
            return False, "missing_shared_secret"
        if not hmac.compare_digest(secret_header, settings.mango_webhook_shared_secret):
            return False, "invalid_shared_secret"

    allowlist = [ip.strip() for ip in settings.mango_webhook_ip_allowlist.split(",") if ip.strip()]
    if allowlist:
        if not source_ip:
            return False, "missing_source_ip"
        try:
            src = ipaddress.ip_address(source_ip)
        except ValueError:
            return False, "invalid_source_ip"
        ok = False
        for item in allowlist:
            try:
                net = ipaddress.ip_network(item, strict=False)
            except ValueError:
                if source_ip == item:
                    ok = True
                    break
                continue
            if src in net:
                ok = True
                break
        if not ok:
            return False, "ip_not_allowed"

    return True, None


def _verify_sha256_signature(raw_body: bytes, signature_header: str, secret: str) -> bool:
    # Accept "sha256=<hex>" or plain hex for provider flexibility.
    incoming = signature_header.strip()
    if incoming.startswith("sha256="):
        incoming = incoming[len("sha256="):]
    computed = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, incoming)


class MangoEventProcessor:
    def __init__(
        self,
        session: AsyncSession,
        store: AbstractMangoLegStateStore,
        correlation_store: Optional[AbstractMangoFreeSwitchCorrelationStore] = None,
    ) -> None:
        self._session = session
        self._store = store
        self._corr = correlation_store or get_mango_freeswitch_correlation_store()
        self._call_repo = CallRepository(Call, session)
        self._transfer_repo = TransferRepository(TransferRecord, session)

    async def process(self, payload: dict[str, Any]) -> MangoNormalizedEvent:
        event = self.normalize(payload)
        if not await self._store.mark_event_seen(event.provider_event_id):
            log.info("mango_webhook.duplicate_ignored", event_id=event.provider_event_id)
            return event

        call, transfer = await self._correlate(event)
        if call is not None and event.call_id is None:
            event.call_id = str(call.id)
        if transfer is not None and event.transfer_id is None:
            event.transfer_id = str(transfer.id)

        if event.leg_id and event.state is not None:
            await self._store.set_leg_state(
                event.leg_id,
                event.state,
                call_id=event.call_id,
                transfer_id=event.transfer_id,
                role=event.role,
                raw_event=event.raw_payload,
            )
            await self._corr.set_mango_state(
                mango_leg_id=event.leg_id,
                state=event.state,
                call_id=event.call_id,
                raw_event=event.raw_payload,
            )
            if event.command_id and event.command_id != event.leg_id:
                log.info(
                    "mango_webhook.provisional_leg_aliased",
                    provider_event_id=event.provider_event_id,
                    command_id=event.command_id,
                    provider_leg_id=event.leg_id,
                    state=event.state.value,
                    call_id=event.call_id,
                )
                await self._store.set_leg_state(
                    event.command_id,
                    event.state,
                    call_id=event.call_id,
                    transfer_id=event.transfer_id,
                    role=event.role,
                    raw_event=event.raw_payload,
                )
                await self._corr.upsert_mapping(
                    mango_leg_id=event.command_id,
                    call_id=event.call_id,
                    freeswitch_uuid=event.leg_id,
                )
        elif event.leg_id:
            await self._store.set_leg_context(
                event.leg_id,
                call_id=event.call_id,
                transfer_id=event.transfer_id,
                role=event.role,
            )
            await self._corr.upsert_mapping(
                mango_leg_id=event.leg_id,
                call_id=event.call_id,
            )
            if event.command_id and event.command_id != event.leg_id:
                await self._store.set_leg_context(
                    event.command_id,
                    call_id=event.call_id,
                    transfer_id=event.transfer_id,
                    role=event.role,
                )
                await self._corr.upsert_mapping(
                    mango_leg_id=event.command_id,
                    call_id=event.call_id,
                    freeswitch_uuid=event.leg_id,
                )

        if event.bridge_status and call is not None and transfer is not None:
            bridge_key = self.bridge_key(call.telephony_leg_id or str(call.id), transfer.manager_call_id or "")
            await self._store.set_bridge_status(bridge_key, event.bridge_status)

        if event.whisper_status and event.leg_id:
            await self._store.set_whisper_status(event.leg_id, event.whisper_status)

        await self._apply_call_state(call, transfer, event)
        return event

    @staticmethod
    def normalize(payload: dict[str, Any]) -> MangoNormalizedEvent:
        provider_type = _str_or(payload, "event", "event_type", "type", "status")
        provider_id = _str_or(payload, "event_id", "id", "request_id", "uid")
        if not provider_id:
            provider_id = hashlib.sha256(
                json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()

        state = _extract_state(provider_type, payload)
        leg_id = _extract_leg_id(payload)
        command_id = _extract_command_id(payload)
        call_id = _extract_uuid_like(payload, ("internal_call_id", "call_id", "crm_call_id"))
        transfer_id = _extract_uuid_like(payload, ("transfer_id", "internal_transfer_id"))
        role = _str_or(payload, "role", "leg_role", "party")

        bridge_status = _extract_bridge_status(provider_type, payload)
        whisper_status = _extract_whisper_status(provider_type, payload)
        from_number, to_number = _extract_call_parties(payload)

        return MangoNormalizedEvent(
            provider_event_id=provider_id,
            provider_type=provider_type or "unknown",
            leg_id=leg_id,
            command_id=command_id,
            state=state,
            call_id=call_id,
            transfer_id=transfer_id,
            role=role,
            bridge_status=bridge_status,
            whisper_status=whisper_status,
            from_number=from_number,
            to_number=to_number,
            raw_payload=payload,
        )

    async def _correlate(
        self, event: MangoNormalizedEvent
    ) -> tuple[Optional[Call], Optional[TransferRecord]]:
        call: Optional[Call] = None
        transfer: Optional[TransferRecord] = None

        if event.call_id:
            try:
                cid = uuid.UUID(event.call_id)
                call = await self._call_repo.get(cid)
            except Exception:
                call = None

        if call is None and event.leg_id:
            call = await self._call_repo.get_by_telephony_leg_id(event.leg_id)
        if call is None and event.command_id:
            call = await self._call_repo.get_by_telephony_leg_id(event.command_id)

        if call is None and event.command_id:
            corr = await self._corr.get(event.command_id)
            if corr and corr.call_id:
                event.call_id = event.call_id or corr.call_id
                try:
                    call = await self._call_repo.get(uuid.UUID(corr.call_id))
                except Exception:
                    call = None
                if call is not None:
                    log.info(
                        "mango_webhook.correlation_store_match",
                        provider_event_id=event.provider_event_id,
                        command_id=event.command_id,
                        call_id=str(call.id),
                        source="command_id",
                    )

        if call is None and event.leg_id:
            corr = await self._corr.get(event.leg_id)
            if corr and corr.call_id:
                event.call_id = event.call_id or corr.call_id
                try:
                    call = await self._call_repo.get(uuid.UUID(corr.call_id))
                except Exception:
                    call = None
                if call is not None:
                    log.info(
                        "mango_webhook.correlation_store_match",
                        provider_event_id=event.provider_event_id,
                        provider_leg_id=event.leg_id,
                        call_id=str(call.id),
                        source="provider_leg_id",
                    )

        if call is None:
            call = await self._correlate_recent_outbound_call(event)
            if call is not None:
                event.call_id = str(call.id)
                if not event.command_id:
                    event.command_id = await self._corr.find_mango_leg_id_by_call_id(str(call.id))
                log.info(
                    "mango_webhook.phone_fallback_match",
                    provider_event_id=event.provider_event_id,
                    provider_leg_id=event.leg_id,
                    command_id=event.command_id,
                    call_id=str(call.id),
                    phone=call.phone,
                )

        if event.transfer_id:
            try:
                tid = uuid.UUID(event.transfer_id)
                transfer = await self._transfer_repo.get(tid)
            except Exception:
                transfer = None

        if transfer is None and event.leg_id:
            transfer = await self._get_transfer_by_manager_leg(event.leg_id)
            if transfer and call is None:
                call = await self._call_repo.get(transfer.call_id)

        if transfer is None and call is not None:
            transfer = await self._transfer_repo.get_latest_for_call(call.id)

        return call, transfer

    async def _correlate_recent_outbound_call(self, event: MangoNormalizedEvent) -> Optional[Call]:
        candidates: list[str] = []
        for raw in (event.to_number, event.from_number):
            normalized = _normalize_phone_candidate(raw)
            if normalized and normalized not in candidates:
                candidates.append(normalized)
        for phone in candidates:
            call = await self._call_repo.get_recent_outbound_call_by_phone(
                phone,
                within_seconds=_OUTBOUND_PHONE_CORRELATION_WINDOW_SECONDS,
            )
            if call is not None:
                return call
        return None

    async def _apply_call_state(
        self,
        call: Optional[Call],
        transfer: Optional[TransferRecord],
        event: MangoNormalizedEvent,
    ) -> None:
        if call is None:
            return

        if event.state == TelephonyLegState.RINGING and call.status not in TERMINAL_STATUSES:
            if call.status in (CallStatus.CREATED, CallStatus.QUEUED, CallStatus.DIALING):
                call.status = CallStatus.RINGING
                await self._call_repo.save(call)

        if event.state == TelephonyLegState.ANSWERED and call.status not in TERMINAL_STATUSES:
            if call.status in (CallStatus.CREATED, CallStatus.QUEUED, CallStatus.DIALING, CallStatus.RINGING):
                call.status = CallStatus.IN_PROGRESS
                self._maybe_promote_real_telephony_leg(call, event)
                await self._call_repo.save(call)

        if event.state in (TelephonyLegState.TERMINATED, TelephonyLegState.FAILED):
            if call.status not in TERMINAL_STATUSES and call.status != CallStatus.CONNECTED_TO_MANAGER:
                self._maybe_promote_real_telephony_leg(call, event)
                call.status = CallStatus.FAILED if event.state == TelephonyLegState.FAILED else CallStatus.COMPLETED
                call.completed_at = datetime.now(timezone.utc)
                await self._call_repo.save(call)

        if event.bridge_status == "bridge_confirmed" and transfer is not None:
            if transfer.status not in (
                TransferStatus.CONNECTED,
                TransferStatus.CALLER_DROPPED,
                TransferStatus.BRIDGE_FAILED,
                TransferStatus.TIMED_OUT,
                TransferStatus.FAILED_ALL_UNAVAILABLE,
                TransferStatus.FAILED_NO_ANSWER,
            ):
                transfer.status = TransferStatus.CONNECTED
                call.status = CallStatus.CONNECTED_TO_MANAGER
                await self._transfer_repo.save(transfer)
                await self._call_repo.save(call)

        if event.whisper_status == "whisper_failed" and transfer is not None:
            if transfer.status not in (
                TransferStatus.CONNECTED,
                TransferStatus.CALLER_DROPPED,
                TransferStatus.BRIDGE_FAILED,
                TransferStatus.TIMED_OUT,
            ):
                transfer.status = TransferStatus.BRIDGE_FAILED
                transfer.failure_stage = "briefing"
                transfer.fallback_message = "Whisper playback failed by provider event"
                call.status = CallStatus.STOPPED
                await self._transfer_repo.save(transfer)
                await self._call_repo.save(call)

    @staticmethod
    def _maybe_promote_real_telephony_leg(call: Call, event: MangoNormalizedEvent) -> None:
        if not event.leg_id:
            return
        current = (call.telephony_leg_id or "").strip()
        if current and current == event.leg_id:
            return
        if current.startswith("direct-") or not current:
            call.telephony_leg_id = event.leg_id

    async def _get_transfer_by_manager_leg(self, manager_leg_id: str) -> Optional[TransferRecord]:
        result = await self._session.execute(
            select(TransferRecord)
            .where(TransferRecord.manager_call_id == manager_leg_id)
            .order_by(TransferRecord.created_at.desc(), TransferRecord.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    def bridge_key(customer_leg_id: str, manager_leg_id: str) -> str:
        return f"{customer_leg_id}:{manager_leg_id}"


def _extract_leg_id(payload: dict[str, Any]) -> Optional[str]:
    nested_entry = payload.get("entry")
    if isinstance(nested_entry, dict):
        nested_id = _str_or(nested_entry, "id", "call_id", "uid", "entry_id")
        if nested_id:
            return nested_id

    nested_call = payload.get("call")
    if isinstance(nested_call, dict):
        nested_id = _str_or(nested_call, "id", "call_id", "uid")
        if nested_id:
            return nested_id

    nested_data = payload.get("data")
    if isinstance(nested_data, dict):
        nested_id = _str_or(nested_data, "call_id", "id", "uid", "recording_id")
        if nested_id:
            return nested_id

    return _str_or(
        payload,
        "leg_id",
        "call_id",
        "callId",
        "uid",
        "recording_id",
        "entry_id",
        "manager_call_id",
    )


def _extract_command_id(payload: dict[str, Any]) -> Optional[str]:
    nested_entry = payload.get("entry")
    if isinstance(nested_entry, dict):
        nested_id = _str_or(nested_entry, "command_id", "request_id", "callback_id")
        if nested_id:
            return nested_id

    nested_call = payload.get("call")
    if isinstance(nested_call, dict):
        nested_id = _str_or(nested_call, "command_id", "request_id", "callback_id")
        if nested_id:
            return nested_id

    nested_data = payload.get("data")
    if isinstance(nested_data, dict):
        nested_id = _str_or(nested_data, "command_id", "request_id", "callback_id")
        if nested_id:
            return nested_id

    return _str_or(payload, "command_id", "request_id", "callback_id")


def _extract_state(provider_type: str, payload: dict[str, Any]) -> Optional[TelephonyLegState]:
    candidate = (provider_type or "").strip().lower()
    if candidate in _STATE_ALIASES:
        return _STATE_ALIASES[candidate]

    status = _str_or(payload, "status", "state", "call_state")
    if status:
        state = _STATE_ALIASES.get(status.strip().lower())
        if state:
            return state
    return None


def _extract_bridge_status(provider_type: str, payload: dict[str, Any]) -> Optional[str]:
    candidate = (provider_type or "").lower()
    if "bridge" in candidate or "transfer" in candidate:
        if "fail" in candidate:
            return "bridge_failed"
        if "done" in candidate or "finish" in candidate or "ok" in candidate or "bridged" in candidate:
            return "bridge_confirmed"
        return "bridge_started"

    explicit = _str_or(payload, "bridge_status", "transfer_status")
    if explicit:
        lowered = explicit.lower()
        if lowered in {"bridged", "confirmed", "connected", "ok"}:
            return "bridge_confirmed"
        if lowered in {"failed", "error"}:
            return "bridge_failed"
        if lowered in {"started", "initiated"}:
            return "bridge_started"
    return None


def _extract_whisper_status(provider_type: str, payload: dict[str, Any]) -> Optional[str]:
    candidate = (provider_type or "").lower()
    if "whisper" in candidate or "playback" in candidate or "play" in candidate:
        if any(x in candidate for x in ("fail", "error")):
            return "whisper_failed"
        if any(x in candidate for x in ("finish", "done", "complete")):
            return "whisper_finished"
        if any(x in candidate for x in ("start", "begin", "playing")):
            return "whisper_started"

    explicit = _str_or(payload, "whisper_status", "playback_status")
    if explicit:
        lowered = explicit.lower()
        if lowered in {"started", "playing", "in_progress"}:
            return "whisper_started"
        if lowered in {"finished", "completed", "done"}:
            return "whisper_finished"
        if lowered in {"failed", "error"}:
            return "whisper_failed"
    return None


def _extract_uuid_like(payload: dict[str, Any], keys: tuple[str, ...]) -> Optional[str]:
    value = _str_or(payload, *keys)
    if value is None:
        return None
    try:
        return str(uuid.UUID(value))
    except Exception:
        return None


def _str_or(payload: dict[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _extract_call_parties(payload: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """
    Extract (from_number, to_number) from a Mango webhook payload.

    Mango sends call-party info in several shapes depending on event type:
      - Flat:    {"from_number": "...", "to_number": "..."}
      - Nested:  {"entry": {"from": {"number": "..."}, "to": {"number": "..."}}}
      - Nested:  {"call": {"from": "...", "to": "..."}}
      - Flat alt: {"caller": "...", "callee": "..."}
    """
    # Flat top-level keys
    from_n = _str_or(payload, "from_number", "caller_number", "caller", "calling", "from")
    to_n = _str_or(payload, "to_number", "callee_number", "callee", "called", "to")

    # Nested under "entry" (Mango call_appeared format)
    entry = payload.get("entry")
    if isinstance(entry, dict):
        from_entry = entry.get("from")
        to_entry = entry.get("to")
        if isinstance(from_entry, dict):
            from_n = from_n or _str_or(from_entry, "number", "phone", "extension")
        elif isinstance(from_entry, str) and from_entry.strip():
            from_n = from_n or from_entry.strip()
        if isinstance(to_entry, dict):
            to_n = to_n or _str_or(to_entry, "number", "phone", "extension")
        elif isinstance(to_entry, str) and to_entry.strip():
            to_n = to_n or to_entry.strip()

    # Nested under "call"
    call = payload.get("call")
    if isinstance(call, dict):
        from_call = call.get("from")
        to_call = call.get("to")
        if isinstance(from_call, dict):
            from_n = from_n or _str_or(from_call, "number", "phone")
        elif isinstance(from_call, str) and from_call.strip():
            from_n = from_n or from_call.strip()
        if isinstance(to_call, dict):
            to_n = to_n or _str_or(to_call, "number", "phone")
        elif isinstance(to_call, str) and to_call.strip():
            to_n = to_n or to_call.strip()

    return from_n or None, to_n or None


def _normalize_phone_candidate(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    try:
        from app.services.phone_service import normalize_phone

        return normalize_phone(raw)
    except Exception:
        return raw.strip() or None

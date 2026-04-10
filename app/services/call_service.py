"""
CallService — all business logic for call lifecycle management.
Coordinates between:
  - CallRepository  (DB reads/writes)
  - AbstractCallEngine (telephony engine)
  - BlockedPhoneRepository (deny list)
  - AuditEvent writes (immutable trail)
No SQL here. No HTTP here. Pure domain logic.
"""
from __future__ import annotations

from typing import Optional
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    BlockedPhoneError,
    EngineError,
    InvalidCallStateError,
    NotFoundError,
    QuietHoursError,
)
from app.core.logging import get_logger
from app.integrations.call_engine.base import AbstractCallEngine
from app.models.audit import AuditEvent
from app.models.agent_profile import AgentProfile
from app.models.blocked_phone import BlockedPhone
from app.models.call import Call, CallMode, CallStatus, TERMINAL_STATUSES
from app.models.steering import SteeringInstruction
from app.repositories.agent_profile_repo import AgentProfileRepository
from app.repositories.blocked_phone_repo import BlockedPhoneRepository
from app.repositories.call_repo import CallRepository
from app.services.phone_service import normalize_phone

log = get_logger(__name__)


def _normalize_browser_label(raw_phone: str) -> str:
    label = (raw_phone or "sandbox").strip().lower()
    sanitized = "".join(ch for ch in label if ch.isalnum() or ch in {"-", "_", ":"})
    sanitized = sanitized[:20] if sanitized else "sandbox"
    if not sanitized.startswith("browser:"):
        sanitized = f"browser:{sanitized}"
    return sanitized[:20]


class CallService:
    def __init__(self, session: AsyncSession, engine: AbstractCallEngine) -> None:
        self.session = session
        self.repo = CallRepository(Call, session)
        self.engine = engine
        self._blocked_repo = BlockedPhoneRepository(BlockedPhone, session)
        self._agent_repo = AgentProfileRepository(AgentProfile, session)

    # ── Create ────────────────────────────────────────────────────────────────

    async def create_call(
        self,
        raw_phone: str,
        mode: CallMode = CallMode.AUTO,
        actor: str = "system",
        agent_profile_id: Optional[uuid.UUID] = None,
    ) -> Call:
        """
        Normalize phone, run pre-call checks, persist a Call, and hand off to engine.

        Raises:
            PhoneNormalizationError: phone cannot be parsed.
            BlockedPhoneError: number is on the deny list.
            QuietHoursError: call is outside the allowed calling window.
        """
        is_browser_call = mode == CallMode.BROWSER
        if is_browser_call:
            phone = _normalize_browser_label(raw_phone)
        else:
            phone = normalize_phone(raw_phone)

        # ── Deny list check ────────────────────────────────────────────────────
        if not is_browser_call and await self._blocked_repo.is_blocked(phone):
            log.warning("call_blocked", phone=phone, actor=actor)
            raise BlockedPhoneError(
                f"Phone number {phone} is on the deny list",
                detail={"phone": phone},
            )

        # ── Quiet hours check ──────────────────────────────────────────────────
        if not is_browser_call:
            _check_quiet_hours()

        agent_profile = None
        if agent_profile_id is not None:
            agent_profile = await self._agent_repo.get(agent_profile_id)
            if agent_profile is None:
                raise NotFoundError(f"Agent profile {agent_profile_id} not found")
            if not agent_profile.is_active:
                raise InvalidCallStateError(
                    f"Agent profile {agent_profile_id} is inactive",
                    detail={"agent_profile_id": str(agent_profile_id)},
                )

        call = Call(
            phone=phone,
            mode=mode,
            status=CallStatus.CREATED,
            agent_profile_id=agent_profile_id,
        )
        if agent_profile is not None:
            call.agent_profile = agent_profile
        await self.repo.save(call)
        await self._audit(call, "created", actor=actor)
        log.info("call_created", call_id=str(call.id), phone=phone, mode=mode)

        # Delegate to engine — RoutingCallEngine selects Vapi / Direct / Stub
        try:
            result = await self.engine.initiate_call(call)
        except Exception as exc:
            call.status = CallStatus.FAILED
            call.completed_at = datetime.now(timezone.utc)
            await self.repo.save(call)
            await self._audit(
                call,
                "status_changed",
                actor="engine",
                payload={"from": CallStatus.CREATED, "to": CallStatus.FAILED},
            )
            log.error(
                "call_create_failed",
                call_id=str(call.id),
                phone=phone,
                mode=str(mode),
                stage="engine_initiate",
                error=str(exc),
            )
            if isinstance(exc, EngineError):
                raise
            raise EngineError(
                "Call initiation failed",
                detail={"call_id": str(call.id), "stage": "engine_initiate", "error": str(exc)},
            ) from exc
        call.status = result.initial_status

        # Store which route was actually used (may differ from mode for AUTO/fallback)
        call.route_used = result.route_used or None

        # Store provider-level telephony leg ID for SIP tracing
        if result.telephony_leg_id:
            call.telephony_leg_id = result.telephony_leg_id

        # Persist external IDs by the actual route that handled the call.
        # This keeps AUTO/fallback flows consistent with stop/steer/status,
        # which already resolve the engine via call.route_used.
        if result.route_used in {"direct", "browser"}:
            call.mango_call_id = result.external_id
            call.vapi_call_id = None
        else:
            call.vapi_call_id = result.external_id
            call.mango_call_id = None

        await self.repo.save(call)
        await self._audit(
            call,
            "status_changed",
            actor="engine",
            payload={"from": CallStatus.CREATED, "to": result.initial_status},
        )
        return call

    # ── Read ──────────────────────────────────────────────────────────────────

    async def get_call(self, call_id: uuid.UUID) -> Call:
        call = await self.repo.get(call_id)
        if call is None:
            raise NotFoundError(f"Call {call_id} not found")
        return call

    async def get_active_calls(self) -> list[Call]:
        return await self.repo.get_active_calls()

    # ── Steer ─────────────────────────────────────────────────────────────────

    async def steer_call(
        self,
        call_id: uuid.UUID,
        instruction: str,
        issued_by: str = "system",
    ) -> SteeringInstruction:
        """
        Send a real-time instruction to the AI and record it.

        Raises:
            NotFoundError: call not found.
            InvalidCallStateError: call is in a terminal state.
        """
        call = await self.get_call(call_id)
        if call.is_terminal():
            raise InvalidCallStateError(
                f"Cannot steer call {call_id}: status is {call.status} (terminal)",
                detail={"call_id": str(call_id), "status": call.status},
            )

        steering = SteeringInstruction(
            call_id=call.id,
            instruction=instruction,
            issued_by=issued_by,
        )
        self.session.add(steering)
        await self.session.flush()

        # Deliver to engine (no-op for StubEngine)
        await self.engine.send_instruction(call, instruction)

        await self._audit(
            call,
            "steered",
            actor=issued_by,
            payload={"instruction": instruction[:200]},
        )
        log.info(
            "call_steered",
            call_id=str(call_id),
            issued_by=issued_by,
            instruction_preview=instruction[:80],
        )
        return steering

    # ── Stop ──────────────────────────────────────────────────────────────────

    async def stop_call(
        self, call_id: uuid.UUID, actor: str = "system"
    ) -> Call:
        """
        Terminate the call and mark it STOPPED.
        Idempotent: if call is already terminal, returns it unchanged.
        """
        call = await self.get_call(call_id)
        if call.status in TERMINAL_STATUSES:
            # Already stopped/completed/failed — idempotent
            return call

        previous_status = call.status
        await self.engine.stop_call(call)
        call.status = CallStatus.STOPPED
        call.completed_at = datetime.now(timezone.utc)
        await self.repo.save(call)
        await self._audit(
            call,
            "stopped",
            actor=actor,
            payload={"from": previous_status, "to": CallStatus.STOPPED},
        )
        log.info("call_stopped", call_id=str(call_id), actor=actor)
        return call

    # ── Helpers ───────────────────────────────────────────────────────────────

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


# ── Standalone helpers ────────────────────────────────────────────────────────

def _check_quiet_hours() -> None:
    """
    Raise QuietHoursError if the current local time is outside the allowed
    calling window defined in settings.

    Does nothing when settings.enforce_quiet_hours is False (default).
    """
    if not settings.enforce_quiet_hours:
        return

    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(settings.calling_timezone)
    except Exception:
        # Unknown timezone — skip enforcement rather than crash
        log.warning("quiet_hours.unknown_timezone", tz=settings.calling_timezone)
        return

    now_local = datetime.now(tz)
    hour = now_local.hour

    if not (settings.calling_hour_start <= hour < settings.calling_hour_end):
        raise QuietHoursError(
            f"Calls are not allowed at this time "
            f"(allowed {settings.calling_hour_start:02d}:00–"
            f"{settings.calling_hour_end:02d}:00 {settings.calling_timezone})",
            detail={
                "current_hour": hour,
                "allowed_from": settings.calling_hour_start,
                "allowed_to": settings.calling_hour_end,
                "timezone": settings.calling_timezone,
            },
        )

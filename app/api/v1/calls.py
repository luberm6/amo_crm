"""
Call endpoints.
POST   /calls                        — initiate a new call
GET    /calls/active                  — list all active calls
GET    /calls/{call_id}               — get call details (with transcript entries)
GET    /calls/{call_id}/card          — compact live card for Telegram bot
POST   /calls/{call_id}/steer         — send real-time steering instruction
POST   /calls/{call_id}/stop          — stop an active call
"""
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.admin_auth import require_admin_auth
from app.api.auth import require_api_key
from app.api.deps import (
    get_abuse_policy,
    get_call_service,
    get_db,
    get_direct_session_manager,
)
from app.core.exceptions import AppError
from app.core.rate_limit import AbusePolicy
from app.models.audit import AuditEvent
from app.models.agent_profile import AgentProfile
from app.models.steering import SteeringInstruction
from app.models.transcript import TranscriptEntry
from app.models.transfer import TransferRecord
from app.repositories.steering_repo import SteeringRepository
from app.repositories.transfer_repo import TransferRepository as TransferRecordRepository
from app.repositories.transcript_repo import TranscriptRepository
from app.schemas.call import (
    CallActiveList,
    CallCardView,
    CallCreate,
    CallCreateResponse,
    CallListItem,
    CallRead,
)
from app.schemas.steering import SteerRequest, SteeringRead
from app.schemas.transcript import TranscriptEntryRead
from app.services.call_service import CallService
router = APIRouter(prefix="/calls", tags=["calls"])
def _handle_app_error(exc: AppError) -> None:
    """Convert domain errors to HTTP responses with consistent shape."""
    raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())
async def _require_calls_auth(request: Request) -> None:
    auth_header = request.headers.get("Authorization")
    if auth_header:
        await require_admin_auth(authorization=auth_header)
        return
    api_key = request.headers.get("x-api-key")
    await require_api_key(x_api_key=api_key)


@router.post(
    "",
    response_model=CallCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_call(
    body: CallCreate,
    request: Request,
    policy: AbusePolicy = Depends(get_abuse_policy),
    service: CallService = Depends(get_call_service),
    session: AsyncSession = Depends(get_db),
) -> CallCreateResponse:
    """Initiate a new outbound AI call."""
    await _require_calls_auth(request)

    agent_profile_id = body.agent_profile_id
    if body.agent_name:
        agent = (
            await session.execute(
                select(AgentProfile)
                .where(AgentProfile.name == body.agent_name)
                .where(AgentProfile.is_active.is_(True))
                .limit(1)
            )
        ).scalar_one_or_none()
        if agent is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "agent_not_found",
                    "message": f"Active agent '{body.agent_name}' not found.",
                },
            )
        agent_profile_id = agent.id

    # Rate limiting check
    api_key = request.headers.get("X-API-Key", "")
    ip = request.client.host if request.client else "unknown"
    try:
        await policy.check_call_create(api_key=api_key, phone=body.phone, ip=ip)
    except AppError as exc:
        _handle_app_error(exc)

    # Create the call
    try:
        runtime_context = (
            {"voice_strategy_override": body.voice_strategy_override}
            if body.voice_strategy_override
            else None
        )
        call = await service.create_call(
            raw_phone=body.phone,
            mode=body.mode,
            agent_profile_id=agent_profile_id,
            runtime_context=runtime_context,
        )
    except AppError as exc:
        _handle_app_error(exc)
    return CallCreateResponse(
        accepted=True,
        id=call.id,
        call_id=call.id,
        phone=call.phone,
        mode=call.mode,
        status=call.status,
        agent_profile_id=call.agent_profile_id,
        route_used=call.route_used,
        telephony_leg_id=call.telephony_leg_id,
        error=None,
    )
@router.get("/active", response_model=CallActiveList)
async def list_active_calls(
    service: CallService = Depends(get_call_service),
) -> CallActiveList:
    """Return all calls that are not in a terminal state."""
    calls = await service.get_active_calls()
    return CallActiveList(
        items=[CallListItem.model_validate(c) for c in calls],
        total=len(calls),
    )
@router.get("/{call_id}/card", response_model=CallCardView)
async def get_call_card(
    call_id: uuid.UUID,
    tail: int = Query(default=5, ge=1, le=20, description="Number of transcript entries to return"),
    service: CallService = Depends(get_call_service),
    session: AsyncSession = Depends(get_db),
) -> CallCardView:
    """
    Compact call view for the Telegram bot live card.
    Returns call state + last N transcript entries + last steering instruction
    in a single response. Optimised for low-latency bot rendering.
    """
    try:
        call = await service.get_call(call_id)
    except AppError as exc:
        _handle_app_error(exc)
    # Transcript tail
    transcript_repo = TranscriptRepository(TranscriptEntry, session)
    all_entries = await transcript_repo.get_by_call(call.id)
    tail_entries = all_entries[-tail:] if all_entries else []
    # Last steering instruction
    steering_repo = SteeringRepository(SteeringInstruction, session)
    last_steering = await steering_repo.get_last_for_call(call.id)
    # Duration: use wall-clock for active calls, stored timestamps for ended ones
    if call.created_at and call.completed_at:
        duration = int((call.completed_at - call.created_at).total_seconds())
    elif call.created_at and call.is_active():
        # Handle both naive and aware datetimes
        now = datetime.now(timezone.utc) if call.created_at.tzinfo else datetime.now()
        duration = int((now - call.created_at).total_seconds())
    else:
        duration = None
    # Latest transfer record (for transfer phase display)
    transfer_repo = TransferRecordRepository(TransferRecord, session)
    latest_transfer = await transfer_repo.get_latest_for_call(call.id)

    return CallCardView(
        id=call.id,
        phone=call.phone,
        mode=call.mode,
        status=call.status,
        is_active=call.is_active(),
        duration_seconds=duration,
        summary=call.summary,
        sentiment=call.sentiment,
        last_instruction=last_steering.instruction if last_steering else None,
        transcript_tail=[TranscriptEntryRead.model_validate(e) for e in tail_entries],
        created_at=call.created_at,
        completed_at=call.completed_at,
        transfer_status=latest_transfer.status.value if latest_transfer else None,
        transfer_failure_reason=latest_transfer.fallback_message if latest_transfer else None,
    )
@router.get("/{call_id}", response_model=CallRead)
async def get_call(
    call_id: uuid.UUID,
    service: CallService = Depends(get_call_service),
    session: AsyncSession = Depends(get_db),
    direct_session_manager=Depends(get_direct_session_manager),
) -> CallRead:
    """
    Fetch full call details including all transcript entries.
    Returns:
    - Call metadata (status, mode, timestamps, duration)
    - Summary and sentiment (set after call ends)
    - All transcript entries in chronological order
    """
    try:
        call = await service.get_call(call_id)
    except AppError as exc:
        _handle_app_error(exc)
    transcript_repo = TranscriptRepository(TranscriptEntry, session)
    entries = await transcript_repo.get_by_call(call.id)
    call_data = CallRead.model_validate(call)
    call_data.transcript_entries = [
        TranscriptEntryRead.model_validate(e) for e in entries
    ]
    runtime_event = (
        await session.execute(
            select(AuditEvent)
            .where(AuditEvent.entity_type == "call")
            .where(AuditEvent.entity_id == call.id)
            .where(AuditEvent.action == "direct_session_finalized")
            .order_by(AuditEvent.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if runtime_event is not None and isinstance(runtime_event.payload, dict):
        payload = runtime_event.payload
        call_data.last_failure_stage = payload.get("stage")
        call_data.last_failure_reason = payload.get("reason")
        call_data.last_disconnect_reason = payload.get("disconnect_reason")
        call_data.last_runtime_error = payload.get("last_error")
    direct_session_id = str(call.mango_call_id or "").strip()
    if direct_session_id:
        live_session = direct_session_manager.get_session(direct_session_id)
        if live_session is not None:
            bridge = live_session.audio_bridge
            voice_state = live_session.voice_state
            capabilities = live_session.capabilities
            metrics = live_session.metrics
            call_data.live_session = {
                "session_id": live_session.session_id,
                "status": live_session.current_status.value,
                "last_failure_stage": live_session.last_failure_stage,
                "last_runtime_error": live_session.last_error,
                "telephony_leg_id": (
                    live_session.telephony_channel.provider_leg_id
                    if live_session.telephony_channel is not None
                    else None
                ),
                "bridge_open": bool(bridge.is_open) if bridge is not None else False,
                "bridge_hangup_reason": (
                    getattr(bridge, "hangup_reason", None) if bridge is not None else None
                ),
                "voice_strategy": voice_state.strategy if voice_state is not None else None,
                "active_voice_path": voice_state.active_path if voice_state is not None else None,
                "awaiting_model_response": metrics.awaiting_model_response,
                "model_turn_active": metrics.model_turn_active,
                "capabilities": {
                    "mode": capabilities.mode,
                    "audio_in": capabilities.audio_in,
                    "audio_out": capabilities.audio_out,
                    "real_audio_in": capabilities.real_audio_in,
                    "real_audio_out": capabilities.real_audio_out,
                },
            }
    return call_data
@router.post(
    "/{call_id}/steer",
    response_model=SteeringRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_api_key)],
)
async def steer_call(
    call_id: uuid.UUID,
    body: SteerRequest,
    request: Request,
    policy: AbusePolicy = Depends(get_abuse_policy),
    service: CallService = Depends(get_call_service),
) -> SteeringRead:
    """
    Send a real-time steering instruction to the AI during the call.
    The instruction is:
    1. Persisted to steering_instructions table
    2. Delivered to the active call engine (VapiEngine injects it as a system message)
    3. Returned in the /card response as last_instruction
    """
    # Rate limiting check
    api_key = request.headers.get("X-API-Key", "")
    try:
        await policy.check_steer(api_key=api_key, call_id=str(call_id))
    except AppError as exc:
        _handle_app_error(exc)

    # Send steering instruction
    try:
        instruction = await service.steer_call(
            call_id=call_id,
            instruction=body.instruction,
            issued_by=body.issued_by,
        )
    except AppError as exc:
        _handle_app_error(exc)
    return SteeringRead.model_validate(instruction)
@router.post(
    "/{call_id}/stop",
    response_model=CallRead,
    dependencies=[Depends(require_api_key)],
)
async def stop_call(
    call_id: uuid.UUID,
    service: CallService = Depends(get_call_service),
) -> CallRead:
    """Terminate an active call immediately."""
    try:
        call = await service.stop_call(call_id=call_id, actor="api")
    except AppError as exc:
        _handle_app_error(exc)
    return CallRead.model_validate(call)

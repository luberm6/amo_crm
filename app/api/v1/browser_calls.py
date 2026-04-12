from __future__ import annotations

import asyncio
import math
import uuid

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audio_utils import Pcm16ChunkAligner, dump_pcm16le_wav, pcm16le_stats
from app.api.admin_auth import require_admin_auth
from app.api.deps import (
    get_browser_registry,
    get_call_service,
    get_db,
    get_direct_session_manager,
)
from app.core.exceptions import AppError
from app.models.call import CallMode
from app.models.transcript import TranscriptEntry
from app.repositories.transcript_repo import TranscriptRepository
from app.schemas.browser_call import (
    BrowserCallCreate,
    BrowserCallDebugActionRead,
    BrowserCallDebugRead,
    BrowserCallRead,
    BrowserCallStartResponse,
)
from app.schemas.transcript import TranscriptEntryRead
from app.services.call_service import CallService
from app.core.logging import get_logger
from app.integrations.voice.stub import StubVoiceProvider

router = APIRouter(prefix="/browser-calls", tags=["browser-calls"])
log = get_logger(__name__)
_PCM_SAMPLE_RATE = 16000
_PCM_CHUNK_BYTES = 4096


def _chunk_pcm16(pcm: bytes, chunk_bytes: int = _PCM_CHUNK_BYTES) -> list[bytes]:
    return [pcm[index:index + chunk_bytes] for index in range(0, len(pcm), chunk_bytes) if pcm[index:index + chunk_bytes]]


def _generate_sine_pcm16(
    *,
    frequency_hz: float = 440.0,
    duration_seconds: float = 1.0,
    sample_rate: int = _PCM_SAMPLE_RATE,
    amplitude: float = 0.68,
) -> bytes:
    total_samples = max(1, int(duration_seconds * sample_rate))
    pcm = bytearray(total_samples * 2)
    for index in range(total_samples):
        sample = math.sin((2.0 * math.pi * frequency_hz * index) / sample_rate)
        pcm_value = int(max(-1.0, min(1.0, sample * amplitude)) * 32767)
        pcm[index * 2:index * 2 + 2] = int(pcm_value).to_bytes(2, "little", signed=True)
    return bytes(pcm)


def _handle_app_error(exc: AppError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())


def _build_browser_read(call, session, bridge, transcript_entries) -> BrowserCallRead:
    metrics = session.metrics if session is not None else None
    voice_state = session.voice_state if session is not None else None
    snapshot = bridge.snapshot() if bridge is not None else None
    return BrowserCallRead(
        call_id=call.id,
        status=call.status,
        label=call.phone,
        agent_profile_id=call.agent_profile_id,
        created_at=call.created_at,
        completed_at=call.completed_at,
        transcript_entries=transcript_entries,
        debug=BrowserCallDebugRead(
            session_id=call.mango_call_id,
            voice_strategy=voice_state.strategy if voice_state is not None else None,
            active_voice_path=voice_state.active_path if voice_state is not None else None,
            primary_voice_path=voice_state.primary_path if voice_state is not None else None,
            fallback_voice_path=voice_state.fallback_path if voice_state is not None else None,
            fallback_used=bool(voice_state.fallback_activated) if voice_state is not None else False,
            session_mode=session.capabilities.mode if session is not None else None,
            websocket_connected=bool(snapshot.client_connected) if snapshot is not None else False,
            bridge_open=bool(snapshot.is_open) if snapshot is not None else False,
            inbound_chunks_received=metrics.inbound_chunks_received if metrics is not None else 0,
            inbound_chunks_sent_to_model=metrics.inbound_chunks_sent_to_model if metrics is not None else 0,
            outbound_chunks_played=metrics.outbound_chunks_played if metrics is not None else 0,
            model_response_latency_ms_last=(
                metrics.model_response_latency_ms_last if metrics is not None else None
            ),
            tts_latency_ms_last=metrics.tts_latency_ms_last if metrics is not None else None,
            outbound_playback_latency_ms_last=(
                metrics.outbound_playback_latency_ms_last if metrics is not None else None
            ),
            last_error=session.last_error if session is not None else None,
            last_failure_stage=session.last_failure_stage if session is not None else None,
            last_disconnect_reason=(
                snapshot.last_disconnect_reason if snapshot is not None else None
            ),
        ),
    )


@router.post(
    "",
    response_model=BrowserCallStartResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin_auth)],
)
async def create_browser_call(
    body: BrowserCallCreate,
    request: Request,
    service: CallService = Depends(get_call_service),
    registry=Depends(get_browser_registry),
    session_manager=Depends(get_direct_session_manager),
) -> BrowserCallStartResponse:
    try:
        call = await service.create_call(
            raw_phone=body.label,
            mode=CallMode.BROWSER,
            actor="browser_admin",
            agent_profile_id=body.agent_profile_id,
        )
    except AppError as exc:
        _handle_app_error(exc)

    bridge = registry.get_bridge(call.id)
    if bridge is None or not call.mango_call_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Browser bridge was not created",
        )
    live_session = session_manager.get_session(call.mango_call_id)
    if bridge is not None:
        bridge.annotate_runtime(
            session_id=call.mango_call_id,
            agent_id=str(call.agent_profile_id) if call.agent_profile_id else None,
            voice_strategy=(
                live_session.voice_state.strategy if live_session and live_session.voice_state else None
            ),
            active_voice_path=(
                live_session.voice_state.active_path if live_session and live_session.voice_state else None
            ),
        )
    base_url = str(request.base_url).rstrip("/")
    ws_scheme = "wss" if request.url.scheme == "https" else "ws"
    ws_url = f"{ws_scheme}://{request.url.netloc}/v1/browser-calls/{call.id}/ws?token={bridge.token}"

    log.info(
        "browser_call.created",
        call_id=str(call.id),
        session_id=call.mango_call_id,
        agent_id=str(call.agent_profile_id) if call.agent_profile_id else None,
        voice_strategy=(
            live_session.voice_state.strategy if live_session and live_session.voice_state else None
        ),
        active_voice_path=(
            live_session.voice_state.active_path if live_session and live_session.voice_state else None
        ),
    )

    return BrowserCallStartResponse(
        call_id=call.id,
        status=call.status,
        session_id=call.mango_call_id,
        agent_profile_id=call.agent_profile_id,
        browser_token=bridge.token,
        websocket_url=ws_url,
        status_url=f"{base_url}/v1/browser-calls/{call.id}",
        stop_url=f"{base_url}/v1/browser-calls/{call.id}/stop",
        voice_strategy=(
            live_session.voice_state.strategy if live_session and live_session.voice_state else "unknown"
        ),
        active_voice_path=(
            live_session.voice_state.active_path if live_session and live_session.voice_state else "unknown"
        ),
        fallback_voice_path=(
            live_session.voice_state.fallback_path if live_session and live_session.voice_state else None
        ),
    )


@router.get(
    "/{call_id}",
    response_model=BrowserCallRead,
    dependencies=[Depends(require_admin_auth)],
)
async def get_browser_call(
    call_id: uuid.UUID,
    service: CallService = Depends(get_call_service),
    db: AsyncSession = Depends(get_db),
    registry=Depends(get_browser_registry),
    session_manager=Depends(get_direct_session_manager),
) -> BrowserCallRead:
    try:
        call = await service.get_call(call_id)
    except AppError as exc:
        _handle_app_error(exc)

    transcript_repo = TranscriptRepository(TranscriptEntry, db)
    entries = [TranscriptEntryRead.model_validate(e) for e in await transcript_repo.get_by_call(call.id)]
    live_session = session_manager.get_session(call.mango_call_id) if call.mango_call_id else None
    bridge = registry.get_bridge(call.id)
    return _build_browser_read(call, live_session, bridge, entries)


@router.post(
    "/{call_id}/stop",
    response_model=BrowserCallRead,
    dependencies=[Depends(require_admin_auth)],
)
async def stop_browser_call(
    call_id: uuid.UUID,
    service: CallService = Depends(get_call_service),
    db: AsyncSession = Depends(get_db),
    registry=Depends(get_browser_registry),
    session_manager=Depends(get_direct_session_manager),
) -> BrowserCallRead:
    try:
        call = await service.stop_call(call_id=call_id, actor="browser_admin")
    except AppError as exc:
        _handle_app_error(exc)

    transcript_repo = TranscriptRepository(TranscriptEntry, db)
    entries = [TranscriptEntryRead.model_validate(e) for e in await transcript_repo.get_by_call(call.id)]
    live_session = session_manager.get_session(call.mango_call_id) if call.mango_call_id else None
    bridge = registry.get_bridge(call.id)
    return _build_browser_read(call, live_session, bridge, entries)


@router.post(
    "/{call_id}/debug/test-tone",
    response_model=BrowserCallDebugActionRead,
    dependencies=[Depends(require_admin_auth)],
)
async def play_browser_test_tone(
    call_id: uuid.UUID,
    service: CallService = Depends(get_call_service),
    registry=Depends(get_browser_registry),
    session_manager=Depends(get_direct_session_manager),
) -> BrowserCallDebugActionRead:
    try:
        call = await service.get_call(call_id)
    except AppError as exc:
        _handle_app_error(exc)

    if not call.mango_call_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Browser session is not active")

    bridge = registry.get_bridge(call.id)
    live_session = session_manager.get_session(call.mango_call_id)
    if bridge is None or live_session is None or live_session.current_status != call.status:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Browser runtime is unavailable")

    chunks = _chunk_pcm16(_generate_sine_pcm16())
    for chunk in chunks:
        await bridge.audio_out(chunk)

    log.info(
        "browser_call.debug_test_tone_sent",
        call_id=str(call.id),
        session_id=call.mango_call_id,
        agent_id=str(call.agent_profile_id) if call.agent_profile_id else None,
        voice_strategy=live_session.voice_state.strategy if live_session.voice_state else None,
        active_voice_path=live_session.voice_state.active_path if live_session.voice_state else None,
        tone_frequency_hz=440.0,
        tone_duration_seconds=1.0,
        tone_amplitude=0.68,
        chunks_enqueued=len(chunks),
    )
    return BrowserCallDebugActionRead(
        action="test_tone",
        message="Backend test tone enqueued for browser playback",
        chunks_enqueued=len(chunks),
    )


@router.post(
    "/{call_id}/debug/test-tts",
    response_model=BrowserCallDebugActionRead,
    dependencies=[Depends(require_admin_auth)],
)
async def play_browser_test_tts(
    call_id: uuid.UUID,
    service: CallService = Depends(get_call_service),
    registry=Depends(get_browser_registry),
    session_manager=Depends(get_direct_session_manager),
) -> BrowserCallDebugActionRead:
    try:
        call = await service.get_call(call_id)
    except AppError as exc:
        _handle_app_error(exc)

    if not call.mango_call_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Browser session is not active")

    bridge = registry.get_bridge(call.id)
    live_session = session_manager.get_session(call.mango_call_id)
    if bridge is None or live_session is None or live_session.current_status != call.status:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Browser runtime is unavailable")

    voice_provider = live_session.voice_provider
    if voice_provider is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Voice provider is unavailable")
    if isinstance(voice_provider, StubVoiceProvider):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="TTS debug playback requires a real voice provider; stub voice returns silence",
        )

    text = "Это тестовое воспроизведение TTS из браузерного debug режима."
    chunks_enqueued = 0
    aligner = Pcm16ChunkAligner()
    prepared_pcm = bytearray()
    try:
        async for chunk in voice_provider.synthesize_streaming(text):
            if not chunk:
                continue
            prepared = aligner.push(chunk)
            if not prepared:
                continue
            prepared_pcm.extend(prepared)
            await bridge.audio_out(prepared)
            chunks_enqueued += 1
        final_chunk = aligner.flush(pad_final_byte=True)
        if final_chunk:
            prepared_pcm.extend(final_chunk)
            await bridge.audio_out(final_chunk)
            chunks_enqueued += 1
    except Exception as exc:
        detail = exc.detail if isinstance(exc, AppError) and isinstance(exc.detail, dict) else {}
        log.error(
            "browser_call.debug_test_tts_failed",
            call_id=str(call.id),
            session_id=call.mango_call_id,
            agent_id=str(call.agent_profile_id) if call.agent_profile_id else None,
            voice_strategy=live_session.voice_state.strategy if live_session.voice_state else None,
            active_voice_path=live_session.voice_state.active_path if live_session.voice_state else None,
            provider=detail.get("provider"),
            stage=detail.get("stage"),
            http_status=detail.get("http_status"),
            content_type=detail.get("content_type"),
            response_bytes=detail.get("byte_length"),
            voice_id_source=detail.get("voice_id_source"),
            voice_id_masked=detail.get("voice_id_masked"),
            api_key_set=detail.get("api_key_set"),
            body_preview=detail.get("body_preview"),
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"TTS debug playback failed: {exc}",
        ) from exc

    log.info(
        "browser_call.debug_test_tts_sent",
        call_id=str(call.id),
        session_id=call.mango_call_id,
        agent_id=str(call.agent_profile_id) if call.agent_profile_id else None,
        voice_strategy=live_session.voice_state.strategy if live_session.voice_state else None,
        active_voice_path=live_session.voice_state.active_path if live_session.voice_state else None,
        chunks_enqueued=chunks_enqueued,
        odd_chunks_seen=aligner.odd_chunks,
        **pcm16le_stats(bytes(prepared_pcm)),
    )
    artifact_path = dump_pcm16le_wav(
        "browser_bridge_outgoing_tts",
        bytes(prepared_pcm),
        session_id=call.mango_call_id,
        call_id=str(call.id),
    )
    if artifact_path:
        log.info(
            "browser_bridge.tts_chunk_sent",
            call_id=str(call.id),
            session_id=call.mango_call_id,
            agent_id=str(call.agent_profile_id) if call.agent_profile_id else None,
            voice_strategy=live_session.voice_state.strategy if live_session.voice_state else None,
            active_voice_path=live_session.voice_state.active_path if live_session.voice_state else None,
            chunk_count=chunks_enqueued,
            odd_chunks_seen=aligner.odd_chunks,
            artifact_path=artifact_path,
        )
    return BrowserCallDebugActionRead(
        action="test_tts",
        message="TTS debug playback enqueued for browser playback",
        chunks_enqueued=chunks_enqueued,
    )


@router.websocket("/{call_id}/ws")
async def browser_call_ws(
    websocket: WebSocket,
    call_id: uuid.UUID,
    token: str,
    registry=Depends(get_browser_registry),
) -> None:
    bridge = registry.get_bridge_by_token(call_id, token)
    if bridge is None:
        await websocket.close(code=4404, reason="browser session not found")
        return

    try:
        bridge.attach_client()
    except RuntimeError:
        await websocket.close(code=4409, reason="browser session already attached")
        return

    await websocket.accept()
    log.info(
        "browser_call.websocket_connected",
        call_id=str(call_id),
        session_id=bridge.session_id,
        agent_id=bridge.agent_id,
        voice_strategy=bridge.voice_strategy,
        active_voice_path=bridge.active_voice_path,
    )

    async def _receiver() -> None:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes"):
                payload = message["bytes"]
                log.info(
                    "browser_call.audio_chunk_received",
                    call_id=str(call_id),
                    session_id=bridge.session_id,
                    agent_id=bridge.agent_id,
                    voice_strategy=bridge.voice_strategy,
                    active_voice_path=bridge.active_voice_path,
                    format="pcm_s16le",
                    sample_rate=_PCM_SAMPLE_RATE,
                    channels=1,
                    sample_width_bits=16,
                    encoding_type="pcm_s16le",
                    container="raw",
                    endian="little",
                    byte_length=len(payload),
                    first_bytes_preview_hex=payload[:12].hex(),
                )
                bridge.push_audio(payload)

    async def _sender() -> None:
        async for chunk in bridge.outbound_audio():
            await websocket.send_bytes(chunk)

    async def _control_sender() -> None:
        async for message in bridge.control_messages():
            try:
                await websocket.send_json(message)
            except (RuntimeError, Exception):
                # WebSocket already closed (e.g. client disconnected before call_ended arrived)
                break

    try:
        await websocket.send_json({"type": "ready", "call_id": str(call_id)})
        await asyncio.gather(_receiver(), _sender(), _control_sender())
    except WebSocketDisconnect:
        pass
    finally:
        log.info(
            "browser_call.websocket_disconnected",
            call_id=str(call_id),
            session_id=bridge.session_id,
            agent_id=bridge.agent_id,
            voice_strategy=bridge.voice_strategy,
            active_voice_path=bridge.active_voice_path,
            reason="browser_disconnect",
        )
        await bridge.detach_client(reason="browser_disconnect")

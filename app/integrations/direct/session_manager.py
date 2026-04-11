"""
DirectSessionManager — управляет активными Gemini Live сессиями.

Phase 2: distributed session coordination via SessionCoordinator.
  - Session metadata persisted in Redis (survives restarts for observability)
  - Ownership lock with TTL — prevents two workers managing the same session
  - Heartbeat background task per session — keeps ownership lease alive
  - Steering via pub/sub — instructions routed to the owning worker
  - Startup reconciliation — orphaned sessions marked FAILED in DB on restart

Phase 1 fallback: if no coordinator is provided, behaves exactly as before
(pure in-process dict).  Used in tests and when Redis is unavailable.

Architecture:
  DirectSession — dataclass with LIVE in-process state (cannot be distributed)
  DirectSessionManager — dict {session_id → DirectSession} + coordinator

Live state (must stay in one process):
  gemini_client, telephony_channel, bg_task, instruction_queue, stop_event

Distributed state (in Redis via coordinator):
  metadata (call_id, phone, status, worker_id, timestamps)
  ownership lock (which worker holds the live connection)
  active set (fast enumeration of all sessions across workers)
  steering channel (pub/sub for cross-worker instruction delivery)

Lifecycle:
  create_session() → DirectSession created + coordinator.register_session()
  inject_instruction() → local fast-path or coordinator.send_steering()
  terminate_session() → graceful shutdown + coordinator.release_session()
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, List, Optional

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.core.exceptions import EngineError
from app.core.logging import get_logger
from app.core.telemetry import (
    inc_direct_audio_in,
    inc_direct_audio_out,
    inc_direct_session_started,
    inc_direct_session_terminated,
    observe_direct_inbound_audio_latency,
    observe_direct_model_response_latency,
    observe_direct_outbound_playback_latency,
    observe_direct_tts_latency,
)
from app.integrations.direct.event_handler import DirectEventHandler
from app.integrations.direct.gemini_client import GeminiLiveClient
from app.integrations.direct.voice_strategy import (
    SessionVoiceState,
    ensure_voice_strategy_valid,
    make_session_voice_state,
    make_session_voice_state_for_strategy,
)
from app.integrations.telephony.audio_bridge import NullAudioBridge
from app.integrations.telephony.base import AbstractTelephonyAdapter, TelephonyChannel
from app.integrations.voice.base import AbstractVoiceProvider
from app.models.call import CallStatus, TERMINAL_STATUSES

if TYPE_CHECKING:
    from app.integrations.telephony.audio_bridge import AbstractAudioBridge

log = get_logger(__name__)

_AUDIO_CHUNK_BYTES = 640
_AUDIO_IN_QUEUE_MAX = 200
_AUDIO_OUT_QUEUE_MAX = 200

# Gemini Live outputs PCM at 24000 Hz; the browser pipeline expects 16000 Hz.
_GEMINI_AUDIO_OUTPUT_RATE = 24000
_BROWSER_AUDIO_RATE = 16000


def _resample_pcm16(data: bytes, from_rate: int, to_rate: int) -> bytes:
    """
    Linear resampling of 16-bit little-endian mono PCM.
    Pure-Python; suitable for small chunks (≤ 4 KB).
    """
    if from_rate == to_rate or not data:
        return data
    import array as _array
    samples = _array.array("h")
    samples.frombytes(data)
    n_in = len(samples)
    n_out = max(1, round(n_in * to_rate / from_rate))
    out = _array.array("h", [0] * n_out)
    for i in range(n_out):
        pos = i * from_rate / to_rate
        lo = int(pos)
        hi = min(lo + 1, n_in - 1)
        frac = pos - lo
        out[i] = round(samples[lo] * (1.0 - frac) + samples[hi] * frac)
    return out.tobytes()


@dataclass
class DirectSessionCapabilities:
    mode: str = "text_only"  # text_only | audio_in_only | audio_out_only | full_duplex
    text_only: bool = True
    audio_in: bool = False
    audio_out: bool = False
    full_duplex: bool = False
    real_audio_in: bool = False
    real_audio_out: bool = False
    real_full_duplex: bool = False


@dataclass
class DirectSessionMetrics:
    inbound_chunks_received: int = 0
    inbound_chunks_sent_to_model: int = 0
    inbound_chunks_dropped: int = 0
    outbound_chunks_enqueued: int = 0
    outbound_chunks_played: int = 0
    outbound_chunks_dropped: int = 0
    inbound_audio_latency_ms_last: Optional[float] = None
    model_response_latency_ms_last: Optional[float] = None
    tts_latency_ms_last: Optional[float] = None
    outbound_playback_latency_ms_last: Optional[float] = None
    last_inbound_sent_at: Optional[float] = None
    last_model_request_at: Optional[float] = None
    awaiting_model_response: bool = False
    last_tts_started_at: Optional[float] = None


@dataclass
class DirectSession:
    """
    In-process state of one active Direct session.

    These fields are NOT serialisable and MUST live in exactly one process:
      gemini_client      — WebSocket connection to Gemini Live
      telephony_channel  — SIP/stub telephony handle
      audio_bridge       — session-scoped media plane (silence or SIP RTP)
      event_handler      — persists transcript entries (has pending asyncio tasks)
      bg_task            — asyncio.Task running the audio loop
      instruction_queue  — asyncio.Queue for steering injections
      stop_event         — asyncio.Event for graceful shutdown signal

    Distributed state (session_id, call_id, phone, status) is mirrored in
    the SessionCoordinator's store so it survives process restart for audit
    and reconciliation purposes.

    session_id — "{call_id}-direct"
    call_id    — UUID of the calls table row
    phone      — E.164 number of the callee
    """
    session_id: str
    call_id: uuid.UUID
    phone: str
    current_status: CallStatus = CallStatus.IN_PROGRESS
    gemini_client: Optional[GeminiLiveClient] = None
    telephony_channel: Optional[TelephonyChannel] = None
    audio_bridge: Optional["AbstractAudioBridge"] = None
    voice_provider: Optional[AbstractVoiceProvider] = None
    event_handler: Optional[DirectEventHandler] = None
    bg_task: Optional[asyncio.Task] = None
    instruction_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    audio_in_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    audio_out_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    capabilities: DirectSessionCapabilities = field(default_factory=DirectSessionCapabilities)
    metrics: DirectSessionMetrics = field(default_factory=DirectSessionMetrics)
    bridge_reader_task: Optional[asyncio.Task] = None
    tts_tasks: set[asyncio.Task] = field(default_factory=set)
    voice_state: Optional[SessionVoiceState] = None
    initial_greeting_text: Optional[str] = None
    last_error: Optional[str] = None
    last_failure_stage: Optional[str] = None


class DirectSessionManager:
    """
    Manages the dict of active DirectSessions for this worker process.

    Singleton per application instance — created once in deps.py and
    injected into every DirectGeminiEngine.

    When a coordinator is provided, all lifecycle events are mirrored to the
    distributed store so other workers and the reconciler have full visibility.
    Without a coordinator (tests / Redis-unavailable) the behaviour is identical
    to the original Phase 1 in-process-only implementation.
    """

    def __init__(
        self,
        coordinator: Optional["SessionCoordinator"] = None,  # type: ignore[name-defined]
    ) -> None:
        self._sessions: dict = {}   # str → DirectSession
        self._coordinator = coordinator

    async def create_session(
        self,
        call_id: uuid.UUID,
        phone: str,
        telephony: AbstractTelephonyAdapter,
        voice: AbstractVoiceProvider,
        session_factory: async_sessionmaker,
        system_prompt: Optional[str] = None,
        initial_greeting_text: Optional[str] = None,
        voice_strategy_name: Optional[str] = None,
    ) -> str:
        """
        Create a new session: connect to Gemini, start background audio loop,
        register with coordinator (if present).

        Raises:
          RuntimeError — if the per-worker session limit is exceeded
          asyncio.TimeoutError — if Gemini does not send setupComplete in time
        """
        if len(self._sessions) >= settings.direct_max_sessions:
            raise RuntimeError(
                f"Превышен лимит одновременных Direct сессий "
                f"({settings.direct_max_sessions})"
            )

        session_id = f"{call_id}-direct"
        effective_prompt = system_prompt or settings.gemini_system_prompt
        strategy_override = voice_strategy_name or None
        voice_definition = ensure_voice_strategy_valid(strategy_override=strategy_override)
        if voice_definition.primary_path == "disabled":
            raise EngineError(
                "Direct call aborted: voice strategy is disabled",
                detail={"call_id": str(call_id), "voice_strategy": voice_definition.strategy},
            )
        voice_state = (
            make_session_voice_state_for_strategy(strategy_override)
            if strategy_override
            else make_session_voice_state()
        )

        channel = await telephony.connect(phone)
        if channel.metadata is None:
            channel.metadata = {}
        channel.metadata["internal_call_id"] = str(call_id)

        # ── Attach audio bridge ───────────────────────────────────────────────
        # If provider bridge is unavailable, degrade to explicit text-only mode.
        try:
            audio_bridge = await telephony.attach_audio_bridge(channel)
        except Exception as exc:
            log.error(
                "session_manager.audio_bridge_attach_failed",
                call_id=str(call_id),
                session_id=session_id,
                stage="bridge_attach",
                error=str(exc),
            )
            try:
                await telephony.disconnect(phone)
            except Exception as disconnect_exc:
                log.warning(
                    "session_manager.audio_bridge_attach_cleanup_failed",
                    call_id=str(call_id),
                    session_id=session_id,
                    stage="bridge_attach_cleanup",
                    error=str(disconnect_exc),
                )
            raise EngineError(
                "Direct call aborted: audio bridge attach failed",
                detail={"call_id": str(call_id), "stage": "bridge_attach", "error": str(exc)},
            ) from exc

        capabilities = self._resolve_capabilities(audio_bridge, voice_state)
        if not capabilities.audio_out:
            log.error(
                "session_manager.audio_out_unavailable",
                call_id=str(call_id),
                session_id=session_id,
                stage="capability_check",
                gemini_audio_output_enabled=settings.gemini_audio_output_enabled,
                elevenlabs_configured=settings.elevenlabs_configured,
            )
            try:
                await audio_bridge.close()
            except Exception:
                pass
            try:
                await telephony.disconnect(phone)
            except Exception:
                pass
            raise EngineError(
                "Direct call aborted: outbound audio path is unavailable",
                detail={"call_id": str(call_id), "stage": "capability_check", "missing": "audio_out"},
            )
        if settings.gemini_audio_input_enabled and not capabilities.audio_in:
            log.error(
                "session_manager.audio_in_unavailable",
                call_id=str(call_id),
                session_id=session_id,
                stage="capability_check",
            )
            try:
                await audio_bridge.close()
            except Exception:
                pass
            try:
                await telephony.disconnect(phone)
            except Exception:
                pass
            raise EngineError(
                "Direct call aborted: inbound audio path is unavailable",
                detail={"call_id": str(call_id), "stage": "capability_check", "missing": "audio_in"},
            )
        event_handler = DirectEventHandler(
            call_id=call_id,
            session_factory=session_factory,
        )

        session = DirectSession(
            session_id=session_id,
            call_id=call_id,
            phone=phone,
            telephony_channel=channel,
            audio_bridge=audio_bridge,
            voice_provider=voice,
            event_handler=event_handler,
            capabilities=capabilities,
            audio_in_queue=asyncio.Queue(maxsize=_AUDIO_IN_QUEUE_MAX),
            audio_out_queue=asyncio.Queue(maxsize=_AUDIO_OUT_QUEUE_MAX),
            voice_state=voice_state,
            initial_greeting_text=(initial_greeting_text or "").strip() or None,
        )
        self._sessions[session_id] = session

        # ── Callbacks: transcript, assistant audio, TTS fallback ────────────
        base_text_cb = event_handler.make_text_callback()

        def on_text(role: str, text: str) -> None:
            base_text_cb(role, text)
            if role != "assistant":
                return
            if session.metrics.awaiting_model_response and session.metrics.last_model_request_at:
                session.metrics.model_response_latency_ms_last = (
                    (time.perf_counter() - session.metrics.last_model_request_at) * 1000
                )
                observe_direct_model_response_latency(
                    session.metrics.model_response_latency_ms_last
                )
                session.metrics.awaiting_model_response = False
                log.info(
                    "session_manager.assistant_reply_started",
                    session_id=session.session_id,
                    call_id=str(session.call_id),
                    voice_strategy=session.voice_state.strategy if session.voice_state else "unknown",
                    voice_path=session.voice_state.active_path if session.voice_state else "unknown",
                    source="assistant_text",
                )
            if (
                session.capabilities.audio_out
                and session.voice_state is not None
                and session.voice_state.wants_tts_for_assistant_text()
            ):
                task = asyncio.create_task(
                    self._synthesize_to_audio_queue(session, voice, text),
                    name=f"tts_{session_id}_{len(session.tts_tasks)+1}",
                )
                session.tts_tasks.add(task)
                task.add_done_callback(lambda t: session.tts_tasks.discard(t))

        def on_audio(pcm: bytes) -> None:
            # Native Gemini audio path (when enabled + audio_out capability).
            if (
                not session.capabilities.audio_out
                or session.voice_state is None
                or not session.voice_state.wants_gemini_audio_output()
            ):
                return
            if session.metrics.awaiting_model_response and session.metrics.last_model_request_at:
                session.metrics.model_response_latency_ms_last = (
                    (time.perf_counter() - session.metrics.last_model_request_at) * 1000
                )
                observe_direct_model_response_latency(
                    session.metrics.model_response_latency_ms_last
                )
                session.metrics.awaiting_model_response = False
                log.info(
                    "session_manager.assistant_reply_started",
                    session_id=session.session_id,
                    call_id=str(session.call_id),
                    voice_strategy=session.voice_state.strategy if session.voice_state else "unknown",
                    voice_path=session.voice_state.active_path if session.voice_state else "unknown",
                    source="gemini_native",
                )
            # Resample Gemini's 24 kHz output to the 16 kHz the browser expects.
            resampled = _resample_pcm16(pcm, _GEMINI_AUDIO_OUTPUT_RATE, _BROWSER_AUDIO_RATE)
            self._enqueue_audio_out(session, resampled, source="gemini_native")

        client = GeminiLiveClient(
            on_text=on_text,
            on_audio=on_audio,
            on_close=lambda: asyncio.get_event_loop().create_task(
                self._on_gemini_close(session_id)
            ),
            audio_modality=bool(
                session.voice_state is not None
                and session.voice_state.wants_gemini_audio_output()
                and session.capabilities.audio_in
            ),
        )
        session.gemini_client = client

        # Connect to Gemini (may raise asyncio.TimeoutError)
        await client.connect(effective_prompt)

        session.bg_task = asyncio.create_task(
            self._run_audio_loop(session),
            name=f"direct_audio_{session_id}",
        )
        await self._start_initial_greeting(session, voice, base_text_cb)

        # ── Distributed coordination ──────────────────────────────────────────
        if self._coordinator is not None:
            try:
                registered = await self._coordinator.register_session(
                    session_id=session_id,
                    call_id=str(call_id),
                    phone=phone,
                )
                if not registered:
                    log.error(
                        "session_manager.coordinator_registration_failed",
                        session_id=session_id,
                        detail="Ownership conflict — proceeding anyway (local only)",
                    )
                else:
                    # Subscribe to remote steering for this session
                    self._coordinator.start_steering_subscriber(
                        session_id, self._sessions
                    )
            except Exception as exc:
                # Coordinator failure must not crash the session — fail open
                log.error(
                    "session_manager.coordinator_error_on_create",
                    session_id=session_id,
                    error=str(exc),
                )

        log.info(
            "session_manager.created",
            session_id=session_id,
            call_id=str(call_id),
            phone=phone,
            active_sessions=len(self._sessions),
            distributed=self._coordinator is not None,
            session_mode=session.capabilities.mode,
            audio_in=session.capabilities.audio_in,
            audio_out=session.capabilities.audio_out,
            voice_strategy=session.voice_state.strategy if session.voice_state else "unknown",
            primary_voice_path=session.voice_state.primary_path if session.voice_state else "unknown",
            fallback_voice_path=session.voice_state.fallback_path if session.voice_state else None,
            active_voice_path=session.voice_state.active_path if session.voice_state else "unknown",
        )
        inc_direct_session_started(session.capabilities.mode)
        return session_id

    async def inject_instruction(self, session_id: str, instruction: str) -> None:
        """
        Inject a steering instruction into an active session.

        Local fast-path: session is in this worker's dict → direct queue.put().
        Remote path (coordinator present): session is owned by another worker →
          publish to Redis pub/sub → owning worker injects locally.
        """
        local = self._sessions.get(session_id)
        if local is not None:
            await local.instruction_queue.put(instruction)
            log.info(
                "session_manager.instruction_queued",
                session_id=session_id,
                preview=instruction[:80],
            )
            return

        if self._coordinator is not None:
            delivered = await self._coordinator.send_steering(
                session_id, instruction, self._sessions
            )
            if not delivered:
                log.warning(
                    "session_manager.inject.not_delivered",
                    session_id=session_id,
                )
        else:
            log.warning(
                "session_manager.inject.session_not_found",
                session_id=session_id,
            )

    async def terminate_session(
        self,
        session_id: str,
        *,
        final_status: Optional[CallStatus] = None,
        stage: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        """
        Gracefully shut down a session.

        Steps:
          1. Signal the audio loop to stop
          2. Cancel the background asyncio.Task
          3. Flush pending transcript entries (don't lose last utterances)
          4. Close the Gemini WebSocket
          5. Release distributed ownership (coordinator)

        Idempotent — calling on an unknown session_id is safe.
        """
        session = self._sessions.pop(session_id, None)
        if session is None:
            return

        log.info(
            "session_manager.terminating",
            session_id=session_id,
            call_id=str(session.call_id),
            final_status=str(final_status or session.current_status),
            stage=stage,
            reason=reason,
        )

        resolved_final_status = final_status
        if resolved_final_status is None:
            if session.current_status in TERMINAL_STATUSES:
                resolved_final_status = session.current_status
            else:
                resolved_final_status = CallStatus.COMPLETED
        session.current_status = resolved_final_status

        session.stop_event.set()

        if session.bg_task and not session.bg_task.done():
            session.bg_task.cancel()
            try:
                await asyncio.wait_for(session.bg_task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        if session.bridge_reader_task and not session.bridge_reader_task.done():
            session.bridge_reader_task.cancel()
            try:
                await asyncio.wait_for(session.bridge_reader_task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        if session.tts_tasks:
            for task in list(session.tts_tasks):
                if not task.done():
                    task.cancel()
            await asyncio.gather(*list(session.tts_tasks), return_exceptions=True)
            session.tts_tasks.clear()

        if session.event_handler:
            await session.event_handler.flush(timeout=3.0)

        if session.gemini_client:
            await session.gemini_client.close()

        # ── Detach audio bridge ───────────────────────────────────────────────
        if session.audio_bridge:
            try:
                await session.audio_bridge.close()
            except Exception as exc:
                log.error(
                    "session_manager.bridge_close_error",
                    session_id=session_id,
                    error=str(exc),
                )

        # ── Finalize call in DB ───────────────────────────────────────────────
        if session.event_handler:
            await session.event_handler.finalize_call(resolved_final_status)

        # ── Release distributed ownership ─────────────────────────────────────
        if self._coordinator is not None:
            try:
                await self._coordinator.release_session(session_id)
            except Exception as exc:
                log.error(
                    "session_manager.coordinator_error_on_terminate",
                    session_id=session_id,
                    error=str(exc),
                )

        log.info("session_manager.terminated", session_id=session_id)
        inc_direct_session_terminated(session.capabilities.mode)

    def get_session(self, session_id: str) -> Optional[DirectSession]:
        return self._sessions.get(session_id)

    def active_count(self) -> int:
        return len(self._sessions)

    def get_session_capabilities(self, session_id: str) -> Optional[DirectSessionCapabilities]:
        session = self._sessions.get(session_id)
        return session.capabilities if session else None

    def get_session_metrics(self, session_id: str) -> Optional[DirectSessionMetrics]:
        session = self._sessions.get(session_id)
        return session.metrics if session else None

    async def suspend_audio(self, session_id: str, reason: str = "transfer") -> None:
        """
        Stop media activity while keeping text steering path alive.
        Useful when control plane transitions the call away from AI dialog.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return
        session.capabilities = DirectSessionCapabilities(
            mode="text_only",
            text_only=True,
            audio_in=False,
            audio_out=False,
            full_duplex=False,
            real_audio_in=False,
            real_audio_out=False,
            real_full_duplex=False,
        )
        if session.voice_state is not None:
            session.voice_state.active_path = "disabled"
        while not session.audio_in_queue.empty():
            session.audio_in_queue.get_nowait()
        while not session.audio_out_queue.empty():
            session.audio_out_queue.get_nowait()
        log.info(
            "session_manager.audio_suspended",
            session_id=session_id,
            reason=reason,
        )

    # ── Private ───────────────────────────────────────────────────────────────

    async def _run_audio_loop(self, session: DirectSession) -> None:
        """
        Production-oriented session loop:
        - bridge audio ingest task with bounded queue + backpressure accounting
        - model audio input dispatch
        - assistant audio output dispatch
        - steering instruction handling in all modes (including text-only)
        """
        assert session.audio_bridge is not None
        assert session.gemini_client is not None

        try:
            if session.capabilities.audio_in:
                session.bridge_reader_task = asyncio.create_task(
                    self._bridge_audio_reader(session),
                    name=f"bridge_reader_{session.session_id}",
                )

            while not session.stop_event.is_set():
                await self._drain_instruction_queue(session)
                await self._drain_audio_in_queue(session)
                await self._drain_audio_out_queue(session)
                await self._check_model_response_timeout(session)
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.exception(
                "session_manager.audio_loop_error",
                call_id=str(session.call_id),
                session_id=session.session_id,
                stage="audio_loop",
                error=str(exc),
            )
            self._schedule_failure(
                session,
                stage="audio_loop",
                error=f"audio loop failed: {exc}",
            )
        finally:
            if session.current_status not in TERMINAL_STATUSES:
                session.current_status = CallStatus.COMPLETED
            # Safety net: if loop exits unexpectedly and the session is still
            # registered, force normal terminate flow to avoid leaked resources.
            if self._sessions.get(session.session_id) is session:
                if session.current_status == CallStatus.FAILED:
                    return
                log.warning(
                    "session_manager.audio_loop_exited_without_terminate",
                    session_id=session.session_id,
                    call_id=str(session.call_id),
                    stop_requested=session.stop_event.is_set(),
                )
                asyncio.create_task(
                    self.terminate_session(session.session_id),
                    name=f"terminate_after_audio_loop_exit_{session.session_id}",
                )

    async def _on_gemini_close(self, session_id: str) -> None:
        """
        Called when Gemini closes the WebSocket (normal or error).
        Removes the session from the local dict.
        Coordinator release is NOT called here — that happens in terminate_session()
        which callers should invoke for clean shutdown.  If the process dies
        before terminate_session() is called, the startup reconciler will clean up.
        """
        session = self._sessions.get(session_id)
        if not session:
            return
        log.info(
            "session_manager.gemini_closed",
            session_id=session_id,
            call_id=str(session.call_id),
        )
        if session.stop_event.is_set():
            await self.terminate_session(session_id)
            return
        self._schedule_failure(
            session,
            stage="gemini_closed",
            error="Gemini websocket closed unexpectedly",
        )

    async def _bridge_audio_reader(self, session: DirectSession) -> None:
        assert session.audio_bridge is not None
        try:
            async for audio_chunk in session.audio_bridge.audio_in():
                if session.stop_event.is_set():
                    return
                if not audio_chunk:
                    continue
                for sub_chunk in self._chunk_pcm(audio_chunk, _AUDIO_CHUNK_BYTES):
                    await self._enqueue_audio_in(session, sub_chunk)
            # Bridge stream closed (usually caller hangup) — terminate session.
            if not session.stop_event.is_set():
                session.stop_event.set()
                log.info(
                    "session_manager.bridge_stream_closed",
                    session_id=session.session_id,
                    reason=getattr(session.audio_bridge, "hangup_reason", None),
                )
                asyncio.create_task(
                    self.terminate_session(session.session_id),
                    name=f"terminate_on_bridge_close_{session.session_id}",
                )
        except asyncio.CancelledError:
            return
        except Exception as exc:
            log.exception(
                "session_manager.bridge_reader_error",
                call_id=str(session.call_id),
                session_id=session.session_id,
                stage="bridge_reader",
                error=str(exc),
            )
            self._schedule_failure(
                session,
                stage="bridge_reader",
                error=f"bridge reader failed: {exc}",
            )

    async def _enqueue_audio_in(self, session: DirectSession, chunk: bytes) -> None:
        session.metrics.inbound_chunks_received += 1
        inc_direct_audio_in("received")
        item = (chunk, time.perf_counter())
        try:
            session.audio_in_queue.put_nowait(item)
            return
        except asyncio.QueueFull:
            session.metrics.inbound_chunks_dropped += 1
            inc_direct_audio_in("dropped")
            try:
                session.audio_in_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                session.audio_in_queue.put_nowait(item)
            except asyncio.QueueFull:
                session.metrics.inbound_chunks_dropped += 1
                inc_direct_audio_in("dropped")

    def _enqueue_audio_out(self, session: DirectSession, chunk: bytes, source: str) -> None:
        if not chunk:
            return
        session.metrics.outbound_chunks_enqueued += 1
        inc_direct_audio_out("enqueued", source)
        item = (chunk, time.perf_counter(), source)
        try:
            session.audio_out_queue.put_nowait(item)
            return
        except asyncio.QueueFull:
            session.metrics.outbound_chunks_dropped += 1
            inc_direct_audio_out("dropped", source)
            try:
                session.audio_out_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                session.audio_out_queue.put_nowait(item)
            except asyncio.QueueFull:
                session.metrics.outbound_chunks_dropped += 1
                inc_direct_audio_out("dropped", source)

    async def _drain_instruction_queue(self, session: DirectSession) -> None:
        assert session.gemini_client is not None
        drained = 0
        while not session.instruction_queue.empty() and drained < 10:
            instruction = session.instruction_queue.get_nowait()
            await session.gemini_client.inject_instruction(instruction)
            drained += 1
            session.metrics.last_model_request_at = time.perf_counter()
            session.metrics.awaiting_model_response = True
            log.info(
                "session_manager.instruction_applied",
                session_id=session.session_id,
                preview=instruction[:80],
            )

    async def _drain_audio_in_queue(self, session: DirectSession) -> None:
        if not session.capabilities.audio_in:
            return
        assert session.gemini_client is not None
        drained = 0
        while not session.audio_in_queue.empty() and drained < 20:
            chunk, enqueued_at = session.audio_in_queue.get_nowait()
            await session.gemini_client.send_audio(chunk)
            drained += 1
            session.metrics.inbound_chunks_sent_to_model += 1
            inc_direct_audio_in("sent_to_model")
            session.metrics.inbound_audio_latency_ms_last = (
                (time.perf_counter() - enqueued_at) * 1000
            )
            observe_direct_inbound_audio_latency(session.metrics.inbound_audio_latency_ms_last)
            session.metrics.last_inbound_sent_at = time.perf_counter()
            session.metrics.last_model_request_at = session.metrics.last_inbound_sent_at
            session.metrics.awaiting_model_response = True

    async def _drain_audio_out_queue(self, session: DirectSession) -> None:
        if not session.capabilities.audio_out:
            return
        assert session.audio_bridge is not None
        drained = 0
        while not session.audio_out_queue.empty() and drained < 20:
            chunk, enqueued_at, _source = session.audio_out_queue.get_nowait()
            await session.audio_bridge.audio_out(chunk)
            drained += 1
            session.metrics.outbound_chunks_played += 1
            inc_direct_audio_out("played", _source)
            session.metrics.outbound_playback_latency_ms_last = (
                (time.perf_counter() - enqueued_at) * 1000
            )
            observe_direct_outbound_playback_latency(
                session.metrics.outbound_playback_latency_ms_last
            )

    async def _synthesize_to_audio_queue(
        self,
        session: DirectSession,
        voice: AbstractVoiceProvider,
        text: str,
        *,
        source_override: Optional[str] = None,
    ) -> None:
        """
        Synthesize text to PCM stream and enqueue for playback.
        Errors are caught and logged — must not crash the session.
        """
        try:
            started = time.perf_counter()
            session.metrics.last_tts_started_at = started
            first_chunk = True
            tts_source = source_override or (
                session.voice_state.active_path
                if session.voice_state is not None
                else "tts_fallback"
            )
            async for pcm in voice.synthesize_streaming(text):
                if first_chunk:
                    session.metrics.tts_latency_ms_last = (
                        (time.perf_counter() - started) * 1000
                    )
                    observe_direct_tts_latency(session.metrics.tts_latency_ms_last)
                    first_chunk = False
                    log.info(
                        "session_manager.tts_reply_started",
                        session_id=session.session_id,
                        call_id=str(session.call_id),
                        voice_strategy=session.voice_state.strategy if session.voice_state else "unknown",
                        voice_path=tts_source,
                    )
                self._enqueue_audio_out(session, pcm, source=tts_source)
        except Exception as exc:
            log.error(
                "session_manager.tts_error",
                call_id=str(session.call_id),
                session_id=session.session_id,
                stage="tts",
                error=str(exc),
            )
            self._schedule_failure(
                session,
                stage="tts",
                error=f"tts failed: {exc}",
            )

    async def _start_initial_greeting(
        self,
        session: DirectSession,
        voice: AbstractVoiceProvider,
        text_callback: Callable[[str, str], None],
    ) -> None:
        greeting = (
            session.initial_greeting_text
            or (settings.direct_initial_greeting_text or "").strip()
        )
        if (
            not settings.direct_initial_greeting_enabled
            or not greeting
            or not session.capabilities.audio_out
            or session.voice_state is None
        ):
            return

        if session.voice_state.initial_greeting_path in {"tts_primary", "tts_fallback"}:
            text_callback("assistant", greeting)
            task = asyncio.create_task(
                self._synthesize_to_audio_queue(
                    session,
                    voice,
                    greeting,
                    source_override=session.voice_state.initial_greeting_path,
                ),
                name=f"initial_greeting_{session.session_id}",
            )
            session.tts_tasks.add(task)
            task.add_done_callback(lambda t: session.tts_tasks.discard(t))
            log.info(
                "session_manager.initial_greeting_started",
                session_id=session.session_id,
                voice_strategy=session.voice_state.strategy,
                path=session.voice_state.initial_greeting_path,
            )
            return

        if (
            session.voice_state.initial_greeting_path == "gemini_native"
            and session.gemini_client is not None
        ):
            instruction = (
                "Сразу после соединения поздоровайся с клиентом этой фразой "
                f"без изменений: {greeting}"
            )
            await session.instruction_queue.put(instruction)
            log.info(
                "session_manager.initial_greeting_started",
                session_id=session.session_id,
                voice_strategy=session.voice_state.strategy,
                path="gemini_native",
            )

    async def _check_model_response_timeout(self, session: DirectSession) -> None:
        timeout = float(settings.direct_model_response_timeout_seconds)
        if timeout <= 0:
            return
        if (
            not session.metrics.awaiting_model_response
            or session.metrics.last_model_request_at is None
        ):
            return
        elapsed = time.perf_counter() - session.metrics.last_model_request_at
        if elapsed < timeout:
            return
        if session.voice_state is not None and session.voice_state.activate_tts_fallback():
            session.metrics.awaiting_model_response = False
            log.warning(
                "session_manager.voice_fallback_activated",
                call_id=str(session.call_id),
                session_id=session.session_id,
                stage="gemini_response_timeout",
                voice_strategy=session.voice_state.strategy,
                active_voice_path=session.voice_state.active_path,
                fallback_from="gemini_native",
            )
            greeting = (
                session.initial_greeting_text
                or (settings.direct_initial_greeting_text or "").strip()
            )
            if (
                session.voice_provider is not None
                and settings.direct_initial_greeting_enabled
                and greeting
                and session.metrics.outbound_chunks_played == 0
            ):
                task = asyncio.create_task(
                    self._synthesize_to_audio_queue(
                        session,
                        session.voice_provider,
                        greeting,
                        source_override="tts_fallback",
                    ),
                    name=f"fallback_greeting_{session.session_id}",
                )
                session.tts_tasks.add(task)
                task.add_done_callback(lambda t: session.tts_tasks.discard(t))
                log.info(
                    "session_manager.initial_greeting_fallback_started",
                    session_id=session.session_id,
                    call_id=str(session.call_id),
                    voice_strategy=session.voice_state.strategy,
                    path="tts_fallback",
                )
            return
        self._schedule_failure(
            session,
            stage="gemini_response_timeout",
            error=f"no Gemini response within {timeout:.2f}s",
        )

    def _schedule_failure(
        self,
        session: DirectSession,
        *,
        stage: str,
        error: str,
    ) -> None:
        if session.current_status == CallStatus.FAILED:
            return
        session.current_status = CallStatus.FAILED
        session.last_error = error
        session.last_failure_stage = stage
        session.stop_event.set()
        log.error(
            "session_manager.session_failed",
            call_id=str(session.call_id),
            session_id=session.session_id,
            stage=stage,
            error=error,
        )
        if self._sessions.get(session.session_id) is session:
            asyncio.create_task(
                self.terminate_session(
                    session.session_id,
                    final_status=CallStatus.FAILED,
                    stage=stage,
                    reason=error,
                ),
                name=f"terminate_failed_session_{session.session_id}",
            )

    def _resolve_capabilities(
        self,
        bridge: "AbstractAudioBridge",
        voice_state: SessionVoiceState,
    ) -> DirectSessionCapabilities:
        bridge_ready = not isinstance(bridge, NullAudioBridge) and bridge.is_open

        audio_in = bool(bridge_ready and settings.gemini_audio_input_enabled)
        audio_out = bool(bridge_ready and voice_state.primary_path != "disabled")
        full_duplex = bool(audio_in and audio_out)
        real_bridge = bool(
            bridge_ready
            and settings.media_gateway_enabled
            and settings.media_gateway_provider == "freeswitch"
            and settings.media_gateway_mode == "esl_rtp"
        )
        real_audio_in = bool(real_bridge and audio_in)
        real_audio_out = bool(real_bridge and audio_out)
        real_full_duplex = bool(real_audio_in and real_audio_out)

        if full_duplex:
            mode = "full_duplex"
        elif audio_in:
            mode = "audio_in_only"
        elif audio_out:
            mode = "audio_out_only"
        else:
            mode = "text_only"

        return DirectSessionCapabilities(
            mode=mode,
            text_only=(mode == "text_only"),
            audio_in=audio_in,
            audio_out=audio_out,
            full_duplex=full_duplex,
            real_audio_in=real_audio_in,
            real_audio_out=real_audio_out,
            real_full_duplex=real_full_duplex,
        )

    @staticmethod
    def _chunk_pcm(pcm: bytes, chunk_size: int) -> list[bytes]:
        if len(pcm) <= chunk_size:
            return [pcm]
        return [pcm[i: i + chunk_size] for i in range(0, len(pcm), chunk_size)]

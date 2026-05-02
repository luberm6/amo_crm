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
import re
import time
import uuid
import zoneinfo
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Callable, List, Optional

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.audio_utils import (
    Pcm16ChunkAligner,
    Pcm16RealtimeOptimizer,
    Pcm16VoicedFirstGate,
    analyze_pcm16_audibility,
    dump_pcm16le_wav,
    pcm16_duration_ms_for_bytes,
    pcm16le_stats,
)
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
from app.integrations.telephony.base import (
    AbstractTelephonyAdapter,
    TelephonyChannel,
    TelephonyLegState,
)
from app.integrations.voice.base import AbstractVoiceProvider
from app.models.call import CallStatus, TERMINAL_STATUSES

if TYPE_CHECKING:
    from app.integrations.telephony.audio_bridge import AbstractAudioBridge

log = get_logger(__name__)

_AUDIO_CHUNK_BYTES = 640

# Incremental TTS: split text at sentence boundaries detected mid-stream.
# Matches whitespace (or end-of-string) that follows terminal punctuation.
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?…])\s+")
# Minimum chars required to fire a TTS call (avoid synthesizing single words).
_MIN_TTS_SENTENCE_CHARS = 12

_DAY_NAMES_RU = {
    0: "понедельник", 1: "вторник", 2: "среда",
    3: "четверг", 4: "пятница", 5: "суббота", 6: "воскресенье",
}


def _session_context_block() -> str:
    """Runtime context injected at the end of every system prompt.

    Provides:
      - Current date/time in the configured timezone (so the model can answer
        questions like "что сейчас?" without guessing).
      - Hangup instruction so the model knows when to call end_call().
    """
    tz = zoneinfo.ZoneInfo(settings.calling_timezone)
    now = datetime.now(tz)
    weekday = _DAY_NAMES_RU[now.weekday()]
    offset = now.strftime("%z")           # "+0300"
    offset_fmt = f"UTC{offset[:3]}:{offset[3:]}"   # "UTC+03:00"
    datetime_line = (
        f"Текущая дата и время: {weekday}, {now.strftime('%d.%m.%Y %H:%M')} "
        f"({settings.calling_timezone}, {offset_fmt})."
    )
    hangup_line = (
        "Когда разговор естественно завершился (клиент попрощался, все вопросы решены "
        "или клиент явно хочет закончить звонок) — вызови функцию end_call."
    )
    return f"{datetime_line}\n{hangup_line}"


_AUDIO_IN_QUEUE_MAX = 200
_AUDIO_OUT_QUEUE_MAX = 200
_AUDIO_IN_DRAIN_BATCH_MAX = 40
_AUDIO_OUT_DRAIN_BATCH_MAX = 100
_AUDIO_LOOP_IDLE_SLEEP_SECONDS = 0.01
_AUDIO_LOOP_ACTIVE_SLEEP_SECONDS = 0.002

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


class IncrementalTTSBuffer:
    """
    Buffers Gemini text fragments and fires a TTS task for each complete sentence.

    Instead of waiting for the full Gemini turn to complete, this splits incoming
    text at sentence boundaries (.  !  ?  …) and calls flush_fn immediately.
    This reduces first-audio latency from ~700-1200 ms to first-sentence latency
    (typically 150-400 ms for short Russian sentences).

    Usage:
      buf = IncrementalTTSBuffer(flush_fn=lambda text: ...)
      buf.push(fragment, is_final=False)  # called per Gemini outputTranscription chunk
      buf.push("", is_final=True)         # called once at turn_complete
      buf.reset()                          # called on interrupted
    """

    def __init__(self, flush_fn: Callable[[str], None], min_chars: int = _MIN_TTS_SENTENCE_CHARS) -> None:
        self._buf: str = ""
        self._flush_fn = flush_fn
        self._min_chars = min_chars

    def push(self, fragment: str, is_final: bool) -> None:
        if fragment:
            self._buf += fragment
        if is_final:
            remaining = self._buf.strip()
            if remaining:
                self._flush_fn(remaining)
            self._buf = ""
            return
        self._try_flush()

    def reset(self) -> None:
        """Discard buffered text (e.g. on user interrupt)."""
        self._buf = ""

    def _try_flush(self) -> None:
        """Flush all complete sentences from the buffer."""
        while True:
            match = _SENTENCE_BOUNDARY_RE.search(self._buf)
            if not match:
                break
            sentence = self._buf[: match.start()].strip()  # includes punctuation
            remainder = self._buf[match.end() :]
            if len(sentence) < self._min_chars:
                # Too short — merge with the next sentence for better prosody.
                break
            self._buf = remainder
            if sentence:
                self._flush_fn(sentence)


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
    inbound_probe_received_logs: int = 0
    inbound_probe_sent_logs: int = 0
    outbound_chunks_enqueued: int = 0
    outbound_chunks_played: int = 0
    outbound_chunks_dropped: int = 0
    inbound_audio_latency_ms_last: Optional[float] = None
    model_response_latency_ms_last: Optional[float] = None
    tts_latency_ms_last: Optional[float] = None
    outbound_playback_latency_ms_last: Optional[float] = None
    tts_first_chunk_sent_ms_last: Optional[float] = None
    tts_provider_first_non_silent_chunk_ms_last: Optional[float] = None
    tts_first_non_silent_chunk_sent_ms_last: Optional[float] = None
    tts_first_non_silent_chunk_played_ms_last: Optional[float] = None
    tts_last_chunk_received_ms_last: Optional[float] = None
    tts_audio_duration_ms_last: Optional[float] = None
    tts_provider_leading_silence_ms_last: Optional[float] = None
    tts_backend_leading_silence_ms_last: Optional[float] = None
    tts_leading_silence_trimmed_ms_last: Optional[float] = None
    tts_trailing_silence_trimmed_ms_last: Optional[float] = None
    tts_chunks_in_last: int = 0
    tts_chunks_out_last: int = 0
    tts_tiny_chunks_in_last: int = 0
    tts_turn_id_last: Optional[str] = None
    last_inbound_sent_at: Optional[float] = None
    last_model_request_at: Optional[float] = None
    awaiting_model_response: bool = False
    model_turn_active: bool = False  # True while Gemini is mid-turn (first audio → turn_complete/interrupted)
    last_tts_started_at: Optional[float] = None
    llm_first_token_ms_last: Optional[float] = None  # ms from user turn to first Gemini text fragment


@dataclass
class OutboundAudioQueueItem:
    chunk: bytes
    enqueued_at: float
    source: str
    turn_id: Optional[str] = None
    contains_first_non_silent_audio: bool = False


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
    telephony_adapter: Optional[AbstractTelephonyAdapter] = None
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
    leg_monitor_task: Optional[asyncio.Task] = None
    tts_tasks: set[asyncio.Task] = field(default_factory=set)
    audio_out_aligners: dict[str, Pcm16ChunkAligner] = field(default_factory=dict)
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
        gemini_voice_name: Optional[str] = None,
        gemini_language_code: str = "ru-RU",
        telephony_caller_id: Optional[str] = None,
        telephony_metadata: Optional[dict] = None,
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
        effective_prompt = (system_prompt or settings.gemini_system_prompt) + "\n\n" + _session_context_block()
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

        channel = await telephony.connect(
            phone,
            caller_id=telephony_caller_id,
            metadata=telephony_metadata,
        )
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
            telephony_adapter=telephony,
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

        # Incremental TTS: fire synthesis per sentence, not per full turn.
        # Only active on the TTS path (transcription_output=True).
        _wants_tts = bool(
            session.voice_state is not None
            and session.voice_state.wants_tts_for_assistant_text()
        )

        def _tts_dispatch(text: str) -> None:
            """Create a TTS task and track it in session.tts_tasks."""
            task = asyncio.create_task(
                self._synthesize_to_audio_queue(session, voice, text),
                name=f"tts_{session_id}_{len(session.tts_tasks) + 1}",
            )
            session.tts_tasks.add(task)
            task.add_done_callback(lambda t: session.tts_tasks.discard(t))

        tts_buffer = (
            IncrementalTTSBuffer(flush_fn=_tts_dispatch)
            if _wants_tts
            else None
        )

        def on_text(role: str, text: str) -> None:
            base_text_cb(role, text)
            # Real-time push to browser — avoid waiting for the 1s polling cycle.
            _bridge = session.audio_bridge
            if _bridge is not None and hasattr(_bridge, "send_control"):
                _bridge.send_control({"type": "transcript", "role": role, "text": text})
            if role != "assistant":
                return
            # Latency accounting — skip if on_text_fragment already fired it.
            if session.metrics.awaiting_model_response and session.metrics.last_model_request_at:
                session.metrics.model_response_latency_ms_last = (
                    (time.perf_counter() - session.metrics.last_model_request_at) * 1000
                )
                observe_direct_model_response_latency(
                    session.metrics.model_response_latency_ms_last
                )
                session.metrics.awaiting_model_response = False
                session.metrics.model_turn_active = True
                log.info(
                    "session_manager.assistant_reply_started",
                    session_id=session.session_id,
                    call_id=str(session.call_id),
                    voice_strategy=session.voice_state.strategy if session.voice_state else "unknown",
                    voice_path=session.voice_state.active_path if session.voice_state else "unknown",
                    source="assistant_text",
                )
            # TTS dispatch — only on legacy (non-streaming) path.
            # On streaming path tts_buffer handles sentence-level dispatch via on_text_fragment.
            if (
                tts_buffer is None
                and session.capabilities.audio_out
                and session.voice_state is not None
                and session.voice_state.wants_tts_for_assistant_text()
            ):
                _tts_dispatch(text)

        def on_text_fragment(role: str, fragment: str, is_final: bool) -> None:
            """Called per outputTranscription chunk (streaming path)."""
            if role != "assistant":
                return
            # First fragment arriving → record LLM first-token latency.
            if fragment and session.metrics.awaiting_model_response and session.metrics.last_model_request_at:
                session.metrics.llm_first_token_ms_last = round(
                    (time.perf_counter() - session.metrics.last_model_request_at) * 1000, 2
                )
                session.metrics.model_response_latency_ms_last = session.metrics.llm_first_token_ms_last
                observe_direct_model_response_latency(session.metrics.model_response_latency_ms_last)
                session.metrics.awaiting_model_response = False
                session.metrics.model_turn_active = True
                log.info(
                    "session_manager.assistant_reply_started",
                    session_id=session.session_id,
                    call_id=str(session.call_id),
                    voice_strategy=session.voice_state.strategy if session.voice_state else "unknown",
                    voice_path=session.voice_state.active_path if session.voice_state else "unknown",
                    source="text_fragment",
                    llm_first_token_ms=session.metrics.llm_first_token_ms_last,
                )
            if tts_buffer is not None:
                tts_buffer.push(fragment, is_final)

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
                session.metrics.model_turn_active = True
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

        def on_interrupted() -> None:
            # Clear buffered outbound audio so the browser stops playing stale speech.
            session.metrics.model_turn_active = False
            # Cancel in-flight TTS tasks (streaming path may have several mid-sentence).
            if session.tts_tasks:
                for _task in list(session.tts_tasks):
                    if not _task.done():
                        _task.cancel()
                session.tts_tasks.clear()
            # Reset incremental TTS buffer so stale fragments are discarded.
            if tts_buffer is not None:
                tts_buffer.reset()
            cleared = 0
            while not session.audio_out_queue.empty():
                try:
                    session.audio_out_queue.get_nowait()
                    cleared += 1
                except asyncio.QueueEmpty:
                    break
            # Signal the browser to cancel already-scheduled playback.
            bridge = session.audio_bridge
            if bridge is not None and hasattr(bridge, "send_control"):
                bridge.send_control({"type": "interrupted"})
            log.debug(
                "session_manager.interrupted",
                session_id=session.session_id,
                cleared_chunks=cleared,
            )

        def on_turn_complete() -> None:
            session.metrics.model_turn_active = False

        def on_tool_call(name: str, args: dict) -> None:
            if name == "end_call":
                log.info(
                    "session_manager.agent_initiated_hangup",
                    session_id=session_id,
                )
                asyncio.get_event_loop().create_task(
                    self.terminate_session(session_id, reason="agent_hangup")
                )

        _wants_audio_out = bool(
            session.voice_state is not None
            and session.voice_state.wants_gemini_audio_output()
        )
        # TTS path: request AUDIO modality + outputAudioTranscription so Gemini returns
        # a text transcript of its speech. We discard Gemini's audio (on_audio returns
        # early when not wants_gemini_audio_output) and pipe the transcript to ElevenLabs.
        # TEXT modality is unsupported by audio-only models (returns error 1011).
        client = GeminiLiveClient(
            on_text=on_text,
            on_audio=on_audio,
            on_close=lambda: asyncio.get_event_loop().create_task(
                self._on_gemini_close(session_id)
            ),
            on_interrupted=on_interrupted,
            on_turn_complete=on_turn_complete,
            on_tool_call=on_tool_call,
            on_text_fragment=on_text_fragment if tts_buffer is not None else None,
            audio_input=bool(session.capabilities.audio_in),
            audio_output=_wants_audio_out,
            transcription_output=_wants_tts,
            voice_name=gemini_voice_name,
            language_code=gemini_language_code,
            model_id=None,
            api_version=None,
        )
        session.gemini_client = client

        # Connect to Gemini (may raise asyncio.TimeoutError)
        await client.connect(effective_prompt)

        session.bg_task = asyncio.create_task(
            self._run_audio_loop(session),
            name=f"direct_audio_{session_id}",
        )
        await self._start_initial_greeting(session)

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

        bridge_disconnect_reason = None
        if session.audio_bridge is not None:
            bridge_disconnect_reason = getattr(session.audio_bridge, "hangup_reason", None)

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

        if session.leg_monitor_task and not session.leg_monitor_task.done():
            session.leg_monitor_task.cancel()
            try:
                await asyncio.wait_for(session.leg_monitor_task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        if session.tts_tasks:
            for task in list(session.tts_tasks):
                if not task.done():
                    task.cancel()
            await asyncio.gather(*list(session.tts_tasks), return_exceptions=True)
            session.tts_tasks.clear()

        if session.event_handler:
            try:
                await session.event_handler.flush(timeout=3.0)
            except Exception as exc:
                log.error(
                    "session_manager.event_flush_failed",
                    session_id=session_id,
                    call_id=str(session.call_id),
                    error=str(exc),
                )

        # Signal the browser that the call ended normally before closing.
        if session.audio_bridge and hasattr(session.audio_bridge, "send_control"):
            session.audio_bridge.send_control({
                "type": "call_ended",
                "reason": reason or "terminated",
            })

        if session.gemini_client:
            try:
                await session.gemini_client.close()
            except Exception as exc:
                log.error(
                    "session_manager.gemini_close_failed",
                    session_id=session_id,
                    call_id=str(session.call_id),
                    error=str(exc),
                )

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

        if session.telephony_adapter is not None and session.telephony_channel is not None:
            leg_id = session.telephony_channel.provider_leg_id
            if leg_id:
                try:
                    await session.telephony_adapter.terminate_leg(leg_id)
                except Exception as exc:
                    log.error(
                        "session_manager.telephony_leg_terminate_error",
                        session_id=session_id,
                        call_id=str(session.call_id),
                        leg_id=leg_id,
                        error=str(exc),
                    )

        # ── Finalize call in DB ───────────────────────────────────────────────
        if session.event_handler:
            try:
                await session.event_handler.finalize_call(
                    resolved_final_status,
                    stage=stage or session.last_failure_stage,
                    reason=reason,
                    disconnect_reason=bridge_disconnect_reason,
                    last_error=session.last_error,
                )
            except Exception as exc:
                log.error(
                    "session_manager.finalize_call_failed",
                    session_id=session_id,
                    call_id=str(session.call_id),
                    error=str(exc),
                )

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
            if (
                session.telephony_adapter is not None
                and session.telephony_channel is not None
                and session.telephony_channel.provider_leg_id
            ):
                session.leg_monitor_task = asyncio.create_task(
                    self._telephony_leg_monitor(session),
                    name=f"leg_monitor_{session.session_id}",
                )

            while not session.stop_event.is_set():
                await self._drain_instruction_queue(session)
                await self._drain_audio_in_queue(session)
                await self._drain_audio_out_queue(session)
                await self._check_model_response_timeout(session)
                if (
                    not session.instruction_queue.empty()
                    or not session.audio_in_queue.empty()
                    or not session.audio_out_queue.empty()
                ):
                    await asyncio.sleep(_AUDIO_LOOP_ACTIVE_SLEEP_SECONDS)
                else:
                    await asyncio.sleep(_AUDIO_LOOP_IDLE_SLEEP_SECONDS)
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

    async def _telephony_leg_monitor(self, session: DirectSession) -> None:
        assert session.telephony_adapter is not None
        assert session.telephony_channel is not None
        leg_id = str(session.telephony_channel.provider_leg_id or "").strip()
        if not leg_id:
            return

        last_state: Optional[TelephonyLegState] = None
        try:
            while not session.stop_event.is_set():
                state = await session.telephony_adapter.get_leg_state(leg_id)
                if state != last_state:
                    log.info(
                        "session_manager.telephony_leg_state_observed",
                        session_id=session.session_id,
                        call_id=str(session.call_id),
                        leg_id=leg_id,
                        state=state.value,
                    )
                    last_state = state

                if state in {TelephonyLegState.ANSWERED, TelephonyLegState.BRIDGED}:
                    if session.current_status != CallStatus.IN_PROGRESS:
                        session.current_status = CallStatus.IN_PROGRESS
                elif state in {TelephonyLegState.TERMINATED, TelephonyLegState.FAILED}:
                    if session.stop_event.is_set():
                        return
                    session.stop_event.set()
                    log.warning(
                        "session_manager.telephony_leg_terminated",
                        session_id=session.session_id,
                        call_id=str(session.call_id),
                        leg_id=leg_id,
                        state=state.value,
                    )
                    asyncio.create_task(
                        self.terminate_session(
                            session.session_id,
                            final_status=CallStatus.FAILED,
                            stage="telephony_leg_terminated",
                            reason=f"telephony leg terminated: {state.value}",
                        ),
                        name=f"terminate_on_leg_state_{session.session_id}",
                    )
                    return

                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            log.warning(
                "session_manager.telephony_leg_monitor_failed",
                session_id=session.session_id,
                call_id=str(session.call_id),
                leg_id=leg_id,
                error=str(exc),
            )

    async def _enqueue_audio_in(self, session: DirectSession, chunk: bytes) -> None:
        session.metrics.inbound_chunks_received += 1
        inc_direct_audio_in("received")
        if session.metrics.inbound_probe_received_logs < 8:
            session.metrics.inbound_probe_received_logs += 1
            stats = pcm16le_stats(chunk)
            log.info(
                "session_manager.audio_in_received_probe",
                call_id=str(session.call_id),
                session_id=session.session_id,
                chunk_index=session.metrics.inbound_chunks_received,
                byte_length=len(chunk),
                queue_depth=session.audio_in_queue.qsize(),
                rms=stats["rms"],
                peak=stats["peak"],
                silence_ratio=stats["silence_ratio"],
                first_bytes_hex=stats["first_bytes_hex"],
            )
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

    def _enqueue_audio_out(
        self,
        session: DirectSession,
        chunk: bytes,
        source: str,
        *,
        turn_id: Optional[str] = None,
        contains_first_non_silent_audio: bool = False,
    ) -> bytes:
        if not chunk:
            return b""
        aligner = session.audio_out_aligners.setdefault(source, Pcm16ChunkAligner())
        aligned = aligner.push(chunk)
        if not aligned:
            log.info(
                "session_manager.tts_audio_prepared",
                call_id=str(session.call_id),
                session_id=session.session_id,
                voice_strategy=session.voice_state.strategy if session.voice_state else None,
                voice_path=source,
                provider=(
                    getattr(session.voice_provider, "runtime_diagnostics", lambda: {})().get("provider")
                    if session.voice_provider is not None
                    else None
                ),
                format="pcm_s16le",
                sample_rate=16000,
                channels=1,
                sample_width_bits=16,
                encoding_type="pcm_s16le",
                endian="little",
                container="raw",
                byte_length=0,
                chunk_count=aligner.chunks_seen,
                first_bytes_preview_hex="",
                carry_bytes=len(aligner.carry),
                odd_chunks_seen=aligner.odd_chunks,
            )
            return b""

        stats = pcm16le_stats(aligned)
        log.info(
            "session_manager.tts_audio_prepared",
            call_id=str(session.call_id),
            session_id=session.session_id,
            voice_strategy=session.voice_state.strategy if session.voice_state else None,
            voice_path=source,
            provider=(
                getattr(session.voice_provider, "runtime_diagnostics", lambda: {})().get("provider")
                if session.voice_provider is not None
                else None
            ),
            format=stats["format"],
            sample_rate=stats["sample_rate"],
            channels=stats["channels"],
            sample_width_bits=stats["sample_width_bits"],
            encoding_type=stats["format"],
            endian=stats["endian"],
            container=stats["container"],
            byte_length=stats["byte_length"],
            chunk_count=aligner.chunks_seen,
            first_bytes_preview_hex=stats["first_bytes_hex"],
            rms=stats["rms"],
            peak=stats["peak"],
            silence_ratio=stats["silence_ratio"],
            clipping_ratio=stats["clipping_ratio"],
            carry_bytes=len(aligner.carry),
            odd_chunks_seen=aligner.odd_chunks,
        )

        session.metrics.outbound_chunks_enqueued += 1
        inc_direct_audio_out("enqueued", source)
        item = OutboundAudioQueueItem(
            chunk=aligned,
            enqueued_at=time.perf_counter(),
            source=source,
            turn_id=turn_id,
            contains_first_non_silent_audio=contains_first_non_silent_audio,
        )
        queue_depth = session.audio_out_queue.qsize()
        if queue_depth >= 40:
            log.warning(
                "session_manager.playback_queue_too_large",
                call_id=str(session.call_id),
                session_id=session.session_id,
                voice_path=source,
                queue_depth=queue_depth,
                buffered_audio_ms=pcm16_duration_ms_for_bytes(sum(
                    len(queued_item.chunk)
                    for queued_item in list(session.audio_out_queue._queue)  # type: ignore[attr-defined]
                    if queued_item.source == source
                )),
            )
        try:
            session.audio_out_queue.put_nowait(item)
            return aligned
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
        return aligned

    def _flush_audio_out_alignment(
        self,
        session: DirectSession,
        source: str,
        *,
        turn_id: Optional[str] = None,
        contains_first_non_silent_audio: bool = False,
    ) -> bytes:
        aligner = session.audio_out_aligners.pop(source, None)
        if aligner is None:
            return b""
        final_chunk = aligner.flush(pad_final_byte=True)
        if not final_chunk:
            return b""
        stats = pcm16le_stats(final_chunk)
        log.warning(
            "session_manager.tts_audio_alignment_flushed",
            call_id=str(session.call_id),
            session_id=session.session_id,
            voice_strategy=session.voice_state.strategy if session.voice_state else None,
            voice_path=source,
            format=stats["format"],
            sample_rate=stats["sample_rate"],
            channels=stats["channels"],
            sample_width_bits=stats["sample_width_bits"],
            encoding_type=stats["format"],
            endian=stats["endian"],
            container=stats["container"],
            byte_length=stats["byte_length"],
            first_bytes_preview_hex=stats["first_bytes_hex"],
            rms=stats["rms"],
            peak=stats["peak"],
            silence_ratio=stats["silence_ratio"],
            clipping_ratio=stats["clipping_ratio"],
            padded_final_byte=True,
        )
        session.metrics.outbound_chunks_enqueued += 1
        inc_direct_audio_out("enqueued", source)
        item = OutboundAudioQueueItem(
            chunk=final_chunk,
            enqueued_at=time.perf_counter(),
            source=source,
            turn_id=turn_id,
            contains_first_non_silent_audio=contains_first_non_silent_audio,
        )
        try:
            session.audio_out_queue.put_nowait(item)
        except asyncio.QueueFull:
            session.metrics.outbound_chunks_dropped += 1
            inc_direct_audio_out("dropped", source)
        return final_chunk

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
        while not session.audio_in_queue.empty() and drained < _AUDIO_IN_DRAIN_BATCH_MAX:
            chunk, enqueued_at = session.audio_in_queue.get_nowait()
            await session.gemini_client.send_audio(chunk)
            drained += 1
            session.metrics.inbound_chunks_sent_to_model += 1
            inc_direct_audio_in("sent_to_model")
            if session.metrics.inbound_probe_sent_logs < 8:
                session.metrics.inbound_probe_sent_logs += 1
                stats = pcm16le_stats(chunk)
                log.info(
                    "session_manager.audio_in_sent_to_model_probe",
                    call_id=str(session.call_id),
                    session_id=session.session_id,
                    chunk_index=session.metrics.inbound_chunks_sent_to_model,
                    byte_length=len(chunk),
                    queue_depth=session.audio_in_queue.qsize(),
                    rms=stats["rms"],
                    peak=stats["peak"],
                    silence_ratio=stats["silence_ratio"],
                    first_bytes_hex=stats["first_bytes_hex"],
                )
            session.metrics.inbound_audio_latency_ms_last = (
                (time.perf_counter() - enqueued_at) * 1000
            )
            observe_direct_inbound_audio_latency(session.metrics.inbound_audio_latency_ms_last)
            session.metrics.last_inbound_sent_at = time.perf_counter()
            session.metrics.last_model_request_at = session.metrics.last_inbound_sent_at
            if not session.metrics.awaiting_model_response and not session.metrics.model_turn_active:
                session.metrics.awaiting_model_response = True

    async def _drain_audio_out_queue(self, session: DirectSession) -> None:
        if not session.capabilities.audio_out:
            return
        assert session.audio_bridge is not None
        drained = 0
        while not session.audio_out_queue.empty() and drained < _AUDIO_OUT_DRAIN_BATCH_MAX:
            item = session.audio_out_queue.get_nowait()
            await session.audio_bridge.audio_out(item.chunk)
            drained += 1
            session.metrics.outbound_chunks_played += 1
            inc_direct_audio_out("played", item.source)
            session.metrics.outbound_playback_latency_ms_last = (
                (time.perf_counter() - item.enqueued_at) * 1000
            )
            observe_direct_outbound_playback_latency(
                session.metrics.outbound_playback_latency_ms_last
            )
            if (
                item.contains_first_non_silent_audio
                and session.metrics.last_tts_started_at is not None
                and item.turn_id == session.metrics.tts_turn_id_last
            ):
                session.metrics.tts_first_non_silent_chunk_played_ms_last = round(
                    (time.perf_counter() - session.metrics.last_tts_started_at) * 1000,
                    2,
                )
                log.info(
                    "tts.first_voiced_chunk_detected",
                    call_id=str(session.call_id),
                    session_id=session.session_id,
                    turn_id=item.turn_id,
                    stage="bridge_played",
                    voice_strategy=session.voice_state.strategy if session.voice_state else None,
                    active_voice_path=item.source,
                    first_non_silent_chunk_played_ms=session.metrics.tts_first_non_silent_chunk_played_ms_last,
                    outbound_playback_latency_ms=round(
                        session.metrics.outbound_playback_latency_ms_last or 0,
                        2,
                    ),
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
        diagnostics_fn = getattr(voice, "runtime_diagnostics", None)
        diagnostics = diagnostics_fn() if callable(diagnostics_fn) else {}
        provider_name = diagnostics.get("provider", type(voice).__name__)
        try:
            started = time.perf_counter()
            session.metrics.last_tts_started_at = started
            session.metrics.tts_turn_id_last = f"{session.session_id}-tts-{int(started * 1000)}"
            first_chunk = True
            first_non_silent_sent = True
            collect_prepared_audio = settings.audio_debug_dump_enabled
            prepared_pcm = bytearray() if collect_prepared_audio else None
            optimizer = Pcm16RealtimeOptimizer()
            startup_gate = Pcm16VoicedFirstGate()
            last_provider_chunk_at: Optional[float] = None
            provider_leading_silence_ms = 0.0
            provider_first_non_silent_seen = False
            tts_source = source_override or (
                session.voice_state.active_path
                if session.voice_state is not None
                else "tts_fallback"
            )
            session.metrics.tts_first_chunk_sent_ms_last = None
            session.metrics.tts_provider_first_non_silent_chunk_ms_last = None
            session.metrics.tts_first_non_silent_chunk_sent_ms_last = None
            session.metrics.tts_first_non_silent_chunk_played_ms_last = None
            session.metrics.tts_provider_leading_silence_ms_last = None
            session.metrics.tts_backend_leading_silence_ms_last = None
            log.info(
                "session_manager.tts_request_started",
                call_id=str(session.call_id),
                session_id=session.session_id,
                provider=provider_name,
                voice_strategy=session.voice_state.strategy if session.voice_state else "unknown",
                voice_path=tts_source,
                config_source=diagnostics.get("config_source"),
                api_key_set=diagnostics.get("api_key_set"),
                voice_id_source=diagnostics.get("voice_id_source"),
                voice_id_masked=diagnostics.get("voice_id_masked"),
                text_chars=len(text),
                turn_id=session.metrics.tts_turn_id_last,
            )
            async for pcm in voice.synthesize_streaming(text):
                last_provider_chunk_at = time.perf_counter()
                if first_chunk:
                    session.metrics.tts_latency_ms_last = (
                        (last_provider_chunk_at - started) * 1000
                    )
                    observe_direct_tts_latency(session.metrics.tts_latency_ms_last)
                    first_chunk = False
                    log.info(
                        "session_manager.tts_reply_started",
                        session_id=session.session_id,
                        call_id=str(session.call_id),
                        provider=provider_name,
                        voice_strategy=session.voice_state.strategy if session.voice_state else "unknown",
                        voice_path=tts_source,
                        turn_id=session.metrics.tts_turn_id_last,
                    )
                provider_analysis = analyze_pcm16_audibility(pcm)
                provider_chunk_duration_ms = pcm16_duration_ms_for_bytes(len(pcm))
                if not provider_first_non_silent_seen:
                    if provider_analysis.first_voiced_sample_index is None:
                        provider_leading_silence_ms = round(
                            provider_leading_silence_ms + provider_chunk_duration_ms,
                            2,
                        )
                        log.info(
                            "tts.leading_silence_detected",
                            call_id=str(session.call_id),
                            session_id=session.session_id,
                            turn_id=session.metrics.tts_turn_id_last,
                            stage="provider",
                            voice_strategy=session.voice_state.strategy if session.voice_state else None,
                            active_voice_path=tts_source,
                            byte_length=len(pcm),
                            rms=round(provider_analysis.rms, 6),
                            peak=round(provider_analysis.peak, 6),
                            silence_class=provider_analysis.silence_class,
                            leading_silence_ms=provider_leading_silence_ms,
                            first_bytes_preview_hex=pcm[:12].hex(),
                        )
                    else:
                        provider_first_non_silent_seen = True
                        provider_leading_silence_ms = round(
                            provider_leading_silence_ms + (provider_analysis.first_voiced_offset_ms or 0.0),
                            2,
                        )
                        session.metrics.tts_provider_first_non_silent_chunk_ms_last = round(
                            ((last_provider_chunk_at - started) * 1000) + (provider_analysis.first_voiced_offset_ms or 0.0),
                            2,
                        )
                        session.metrics.tts_provider_leading_silence_ms_last = provider_leading_silence_ms
                        log.info(
                            "tts.first_voiced_chunk_detected",
                            call_id=str(session.call_id),
                            session_id=session.session_id,
                            turn_id=session.metrics.tts_turn_id_last,
                            stage="provider",
                            voice_strategy=session.voice_state.strategy if session.voice_state else None,
                            active_voice_path=tts_source,
                            first_non_silent_chunk_ms=session.metrics.tts_provider_first_non_silent_chunk_ms_last,
                            leading_silence_ms=provider_leading_silence_ms,
                            byte_length=len(pcm),
                            rms=round(provider_analysis.rms, 6),
                            peak=round(provider_analysis.peak, 6),
                            silence_class=provider_analysis.silence_class,
                            first_voiced_offset_ms=provider_analysis.first_voiced_offset_ms,
                            first_bytes_preview_hex=pcm[:12].hex(),
                        )
                for optimized_chunk in optimizer.push(pcm):
                    emitted_chunks, gate_event = startup_gate.push(optimized_chunk)
                    if gate_event and gate_event.total_leading_trimmed_ms > 0:
                        log.info(
                            "tts.leading_silence_trimmed",
                            call_id=str(session.call_id),
                            session_id=session.session_id,
                            turn_id=session.metrics.tts_turn_id_last,
                            stage="backend_emitted",
                            voice_strategy=session.voice_state.strategy if session.voice_state else None,
                            active_voice_path=tts_source,
                            leading_trimmed_ms=gate_event.leading_trimmed_ms,
                            total_leading_trimmed_ms=gate_event.total_leading_trimmed_ms,
                            dropped_chunks=gate_event.dropped_chunks,
                            silent_chunks_dropped=gate_event.silent_chunks_dropped,
                            near_silent_chunks_dropped=gate_event.near_silent_chunks_dropped,
                        )
                    if gate_event:
                        session.metrics.tts_backend_leading_silence_ms_last = gate_event.total_leading_trimmed_ms
                        log.info(
                            "startup_policy.voiced_first_engaged",
                            call_id=str(session.call_id),
                            session_id=session.session_id,
                            turn_id=session.metrics.tts_turn_id_last,
                            voice_strategy=session.voice_state.strategy if session.voice_state else None,
                            active_voice_path=tts_source,
                            dropped_chunks=gate_event.dropped_chunks,
                            silent_chunks_dropped=gate_event.silent_chunks_dropped,
                            near_silent_chunks_dropped=gate_event.near_silent_chunks_dropped,
                            first_voiced_offset_ms=gate_event.first_voiced_offset_ms,
                            leading_trimmed_ms=gate_event.total_leading_trimmed_ms,
                        )
                    for emitted_chunk in emitted_chunks:
                        aligned = self._enqueue_audio_out(
                            session,
                            emitted_chunk,
                            source=tts_source,
                            turn_id=session.metrics.tts_turn_id_last,
                            contains_first_non_silent_audio=gate_event is not None,
                        )
                        if not aligned:
                            continue
                        if gate_event is not None and first_non_silent_sent:
                            session.metrics.tts_first_chunk_sent_ms_last = round(
                                (time.perf_counter() - started) * 1000,
                                2,
                            )
                            session.metrics.tts_first_non_silent_chunk_sent_ms_last = (
                                session.metrics.tts_first_chunk_sent_ms_last
                            )
                            session.metrics.tts_backend_leading_silence_ms_last = round(
                                gate_event.total_leading_trimmed_ms,
                                2,
                            )
                            first_non_silent_sent = False
                            log.info(
                                "tts.first_voiced_chunk_detected",
                                call_id=str(session.call_id),
                                session_id=session.session_id,
                                turn_id=session.metrics.tts_turn_id_last,
                                stage="backend_emitted",
                                voice_strategy=session.voice_state.strategy if session.voice_state else None,
                                active_voice_path=tts_source,
                                first_non_silent_chunk_sent_ms=session.metrics.tts_first_non_silent_chunk_sent_ms_last,
                                leading_silence_ms=session.metrics.tts_backend_leading_silence_ms_last,
                                emitted_bytes=len(aligned),
                                silence_class=gate_event.silence_class,
                                first_voiced_offset_ms=gate_event.first_voiced_offset_ms,
                            )
                            if session.audio_bridge and hasattr(session.audio_bridge, "send_control"):
                                session.audio_bridge.send_control({
                                    "type": "tts_turn_metrics",
                                    "phase": "started",
                                    "turn_id": session.metrics.tts_turn_id_last,
                                    "voice_path": tts_source,
                                    "tts_first_chunk_received_ms": round(session.metrics.tts_latency_ms_last or 0, 2),
                                    "tts_provider_first_non_silent_chunk_ms": session.metrics.tts_provider_first_non_silent_chunk_ms_last,
                                    "tts_first_chunk_sent_to_bridge_ms": session.metrics.tts_first_chunk_sent_ms_last,
                                    "tts_first_non_silent_chunk_sent_ms": session.metrics.tts_first_non_silent_chunk_sent_ms_last,
                                    "tts_provider_leading_silence_ms": session.metrics.tts_provider_leading_silence_ms_last,
                                    "tts_backend_leading_silence_ms": session.metrics.tts_backend_leading_silence_ms_last,
                                })
                        if prepared_pcm is not None:
                            prepared_pcm.extend(aligned)
            optimized_tail, chunking = optimizer.flush()
            for optimized_chunk in optimized_tail:
                emitted_chunks, gate_event = startup_gate.push(optimized_chunk)
                if gate_event and gate_event.total_leading_trimmed_ms > 0:
                    log.info(
                        "tts.leading_silence_trimmed",
                        call_id=str(session.call_id),
                        session_id=session.session_id,
                        turn_id=session.metrics.tts_turn_id_last,
                        stage="backend_emitted_tail",
                        voice_strategy=session.voice_state.strategy if session.voice_state else None,
                        active_voice_path=tts_source,
                        leading_trimmed_ms=gate_event.leading_trimmed_ms,
                        total_leading_trimmed_ms=gate_event.total_leading_trimmed_ms,
                        dropped_chunks=gate_event.dropped_chunks,
                        silent_chunks_dropped=gate_event.silent_chunks_dropped,
                        near_silent_chunks_dropped=gate_event.near_silent_chunks_dropped,
                    )
                if gate_event:
                    session.metrics.tts_backend_leading_silence_ms_last = gate_event.total_leading_trimmed_ms
                for emitted_chunk in emitted_chunks:
                    aligned = self._enqueue_audio_out(
                        session,
                        emitted_chunk,
                        source=tts_source,
                        turn_id=session.metrics.tts_turn_id_last,
                        contains_first_non_silent_audio=gate_event is not None,
                    )
                    if not aligned:
                        continue
                    if gate_event is not None and first_non_silent_sent:
                        session.metrics.tts_first_chunk_sent_ms_last = round(
                            (time.perf_counter() - started) * 1000,
                            2,
                        )
                        session.metrics.tts_first_non_silent_chunk_sent_ms_last = (
                            session.metrics.tts_first_chunk_sent_ms_last
                        )
                        first_non_silent_sent = False
                        log.info(
                            "tts.first_voiced_chunk_detected",
                            call_id=str(session.call_id),
                            session_id=session.session_id,
                            turn_id=session.metrics.tts_turn_id_last,
                            stage="backend_emitted_tail",
                            voice_strategy=session.voice_state.strategy if session.voice_state else None,
                            active_voice_path=tts_source,
                            first_non_silent_chunk_sent_ms=session.metrics.tts_first_non_silent_chunk_sent_ms_last,
                            leading_silence_ms=session.metrics.tts_backend_leading_silence_ms_last,
                            emitted_bytes=len(aligned),
                            silence_class=gate_event.silence_class,
                            first_voiced_offset_ms=gate_event.first_voiced_offset_ms,
                        )
                        if session.audio_bridge and hasattr(session.audio_bridge, "send_control"):
                            session.audio_bridge.send_control({
                                "type": "tts_turn_metrics",
                                "phase": "started",
                                "turn_id": session.metrics.tts_turn_id_last,
                                "voice_path": tts_source,
                                "tts_first_chunk_received_ms": round(session.metrics.tts_latency_ms_last or 0, 2),
                                "tts_provider_first_non_silent_chunk_ms": session.metrics.tts_provider_first_non_silent_chunk_ms_last,
                                "tts_first_chunk_sent_to_bridge_ms": session.metrics.tts_first_chunk_sent_ms_last,
                                "tts_first_non_silent_chunk_sent_ms": session.metrics.tts_first_non_silent_chunk_sent_ms_last,
                                "tts_provider_leading_silence_ms": session.metrics.tts_provider_leading_silence_ms_last,
                                "tts_backend_leading_silence_ms": session.metrics.tts_backend_leading_silence_ms_last,
                            })
                    if prepared_pcm is not None:
                        prepared_pcm.extend(aligned)
            final_chunk = self._flush_audio_out_alignment(
                session,
                tts_source,
                turn_id=session.metrics.tts_turn_id_last,
            )
            if prepared_pcm is not None and final_chunk:
                prepared_pcm.extend(final_chunk)

            session.metrics.tts_last_chunk_received_ms_last = (
                (last_provider_chunk_at - started) * 1000 if last_provider_chunk_at is not None else None
            )
            session.metrics.tts_audio_duration_ms_last = chunking.emitted_audio_duration_ms
            session.metrics.tts_provider_leading_silence_ms_last = round(provider_leading_silence_ms, 2)
            session.metrics.tts_backend_leading_silence_ms_last = round(
                startup_gate.snapshot().leading_silence_trimmed_ms,
                2,
            )
            session.metrics.tts_leading_silence_trimmed_ms_last = round(
                chunking.leading_silence_trimmed_ms + startup_gate.snapshot().leading_silence_trimmed_ms,
                2,
            )
            session.metrics.tts_trailing_silence_trimmed_ms_last = chunking.trailing_silence_trimmed_ms
            session.metrics.tts_chunks_in_last = chunking.chunks_in
            session.metrics.tts_chunks_out_last = max(
                0,
                chunking.chunks_out - startup_gate.snapshot().dropped_chunks,
            )
            session.metrics.tts_tiny_chunks_in_last = chunking.tiny_chunks_in

            log.info(
                "session_manager.tts_turn_completed",
                call_id=str(session.call_id),
                session_id=session.session_id,
                turn_id=session.metrics.tts_turn_id_last,
                provider=provider_name,
                voice_strategy=session.voice_state.strategy if session.voice_state else "unknown",
                voice_path=tts_source,
                raw_chunks_in=chunking.chunks_in,
                tiny_chunks_in=chunking.tiny_chunks_in,
                optimized_chunks_out=session.metrics.tts_chunks_out_last,
                raw_bytes_in=chunking.bytes_in,
                optimized_bytes_out=chunking.bytes_out,
                tts_first_chunk_received_ms=round(session.metrics.tts_latency_ms_last or 0, 2),
                tts_first_chunk_sent_to_bridge_ms=round(session.metrics.tts_first_chunk_sent_ms_last or 0, 2),
                tts_provider_first_non_silent_chunk_ms=session.metrics.tts_provider_first_non_silent_chunk_ms_last,
                tts_first_non_silent_chunk_sent_ms=session.metrics.tts_first_non_silent_chunk_sent_ms_last,
                tts_first_non_silent_chunk_played_ms=session.metrics.tts_first_non_silent_chunk_played_ms_last,
                tts_last_chunk_received_ms=(
                    round(session.metrics.tts_last_chunk_received_ms_last, 2)
                    if session.metrics.tts_last_chunk_received_ms_last is not None
                    else None
                ),
                emitted_audio_duration_ms=chunking.emitted_audio_duration_ms,
                provider_leading_silence_ms=session.metrics.tts_provider_leading_silence_ms_last,
                backend_leading_silence_ms=session.metrics.tts_backend_leading_silence_ms_last,
                leading_silence_trimmed_ms=session.metrics.tts_leading_silence_trimmed_ms_last,
                trailing_silence_trimmed_ms=chunking.trailing_silence_trimmed_ms,
                trailing_silence_kept_ms=chunking.trailing_silence_kept_ms,
                silence_chunk_ratio=round(chunking.silence_chunk_ratio, 4),
                stream_delivery_ratio=round(
                    (
                        chunking.emitted_audio_duration_ms
                        / max(1.0, session.metrics.tts_last_chunk_received_ms_last or 1.0)
                    ),
                    3,
                ),
            )
            if chunking.tiny_chunks_in >= 24:
                log.warning(
                    "session_manager.tts_chunking_overfragmented",
                    call_id=str(session.call_id),
                    session_id=session.session_id,
                    turn_id=session.metrics.tts_turn_id_last,
                    provider=provider_name,
                    tiny_chunks_in=chunking.tiny_chunks_in,
                    raw_chunks_in=chunking.chunks_in,
                    optimized_chunks_out=chunking.chunks_out,
                )
            if chunking.trailing_silence_trimmed_ms >= 120:
                log.warning(
                    "session_manager.trailing_silence_excessive",
                    call_id=str(session.call_id),
                    session_id=session.session_id,
                    turn_id=session.metrics.tts_turn_id_last,
                    provider=provider_name,
                    trailing_silence_trimmed_ms=chunking.trailing_silence_trimmed_ms,
                )
            if session.audio_bridge and hasattr(session.audio_bridge, "send_control"):
                session.audio_bridge.send_control({
                    "type": "tts_turn_metrics",
                    "phase": "completed",
                    "turn_id": session.metrics.tts_turn_id_last,
                    "voice_path": tts_source,
                    "tts_first_chunk_received_ms": round(session.metrics.tts_latency_ms_last or 0, 2),
                    "tts_first_chunk_sent_to_bridge_ms": round(session.metrics.tts_first_chunk_sent_ms_last or 0, 2),
                    "tts_provider_first_non_silent_chunk_ms": session.metrics.tts_provider_first_non_silent_chunk_ms_last,
                    "tts_first_non_silent_chunk_sent_ms": session.metrics.tts_first_non_silent_chunk_sent_ms_last,
                    "tts_first_non_silent_chunk_played_ms": session.metrics.tts_first_non_silent_chunk_played_ms_last,
                    "tts_last_chunk_received_ms": (
                        round(session.metrics.tts_last_chunk_received_ms_last, 2)
                        if session.metrics.tts_last_chunk_received_ms_last is not None
                        else None
                    ),
                    "emitted_audio_duration_ms": chunking.emitted_audio_duration_ms,
                    "leading_silence_trimmed_ms": session.metrics.tts_leading_silence_trimmed_ms_last,
                    "tts_provider_leading_silence_ms": session.metrics.tts_provider_leading_silence_ms_last,
                    "tts_backend_leading_silence_ms": session.metrics.tts_backend_leading_silence_ms_last,
                    "trailing_silence_trimmed_ms": chunking.trailing_silence_trimmed_ms,
                    "raw_chunks_in": chunking.chunks_in,
                    "optimized_chunks_out": session.metrics.tts_chunks_out_last,
                })
            if prepared_pcm:
                artifact_path = dump_pcm16le_wav(
                    "backend_prepared_tts",
                    bytes(prepared_pcm),
                    session_id=session.session_id,
                    call_id=str(session.call_id),
                )
                if artifact_path:
                    log.info(
                        "session_manager.tts_audio_dumped",
                        call_id=str(session.call_id),
                        session_id=session.session_id,
                        voice_strategy=session.voice_state.strategy if session.voice_state else None,
                        voice_path=tts_source,
                        artifact_path=artifact_path,
                    )
        except Exception as exc:
            detail = exc.detail if isinstance(exc, EngineError) and isinstance(exc.detail, dict) else {}
            tts_stage = str(detail.get("stage") or "tts")
            failure_stage = f"tts_{tts_stage}"
            log.error(
                "session_manager.tts_error",
                call_id=str(session.call_id),
                session_id=session.session_id,
                stage=failure_stage,
                provider=detail.get("provider", provider_name),
                error=str(exc),
                config_source=detail.get("config_source", diagnostics.get("config_source")),
                api_key_set=detail.get("api_key_set", diagnostics.get("api_key_set")),
                voice_id_source=detail.get("voice_id_source", diagnostics.get("voice_id_source")),
                voice_id_masked=detail.get("voice_id_masked", diagnostics.get("voice_id_masked")),
                http_status=detail.get("http_status"),
                content_type=detail.get("content_type"),
                response_bytes=detail.get("byte_length"),
                body_preview=detail.get("body_preview"),
            )
            self._schedule_failure(
                session,
                stage=failure_stage,
                error=f"tts failed: {exc}",
            )

    async def _start_initial_greeting(
        self,
        session: DirectSession,
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

        path = session.voice_state.initial_greeting_path
        if path in {"tts_primary", "tts_fallback"} and session.voice_provider is not None:
            # For TTS-first strategies, synthesize the greeting directly.
            # This avoids coupling call survival to Gemini's first-token latency
            # right after answer, which can otherwise terminate a live PSTN leg
            # before the caller hears anything.
            task = asyncio.create_task(
                self._synthesize_to_audio_queue(
                    session,
                    session.voice_provider,
                    greeting,
                    source_override=path,
                ),
                name=f"initial_greeting_tts_{session.session_id}",
            )
            session.tts_tasks.add(task)
            task.add_done_callback(lambda t: session.tts_tasks.discard(t))
            log.info(
                "session_manager.initial_greeting_started",
                session_id=session.session_id,
                voice_strategy=session.voice_state.strategy,
                path=path,
                mode="direct_tts",
            )
            return

        if path == "gemini_native" and session.gemini_client is not None:
            instruction = (
                "Сразу после соединения поздоровайся с клиентом этой фразой "
                f"без изменений: {greeting}"
            )
            await session.instruction_queue.put(instruction)
            log.info(
                "session_manager.initial_greeting_started",
                session_id=session.session_id,
                voice_strategy=session.voice_state.strategy,
                path=path,
                mode="gemini_instruction",
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
        if (
            session.voice_state is not None
            and session.voice_state.wants_tts_output()
            and session.voice_provider is not None
        ):
            session.metrics.awaiting_model_response = False
            session.metrics.model_turn_active = False
            session.metrics.last_model_request_at = None
            log.warning(
                "session_manager.model_response_timeout_ignored_on_tts_path",
                call_id=str(session.call_id),
                session_id=session.session_id,
                stage="gemini_response_timeout",
                voice_strategy=session.voice_state.strategy,
                active_voice_path=session.voice_state.active_path,
                elapsed_seconds=round(elapsed, 3),
                timeout_seconds=round(timeout, 3),
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

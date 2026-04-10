"""
FastAPI dependency injection.

Engine selection logic (get_call_engine):
  Возвращает RoutingCallEngine — он сам выбирает sub-engine по call.mode.

  - VAPI mode / Vapi сконфигурирован  → VapiCallEngine
  - DIRECT mode / Gemini сконфигурирован → DirectGeminiEngine
  - AUTO → Vapi → Direct → Stub (в порядке приоритета)
  - Если ничего не настроено → StubEngine (безопасный fallback)

Singleton DirectSessionManager:
  DirectSessionManager хранит in-memory состояние всех активных Direct сессий.
  Должен быть один на весь процесс — создаётся один раз при первом обращении.
  При горизонтальном масштабировании (Phase 3) — перенести в Redis.

Конфигурация:
  Vapi:   VAPI_API_KEY + VAPI_ASSISTANT_ID + VAPI_PHONE_NUMBER_ID
  Direct: GEMINI_API_KEY
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Optional

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.rate_limit import AbusePolicy
from app.core.redis_client import get_redis
from app.db.session import get_db
from app.integrations.call_engine.base import AbstractCallEngine

log = get_logger(__name__)
from app.integrations.call_engine.stub import StubEngine
from app.integrations.transfer_engine.base import AbstractTransferEngine
from app.integrations.transfer_engine.mango import MangoTransferEngine
from app.integrations.transfer_engine.stub import StubTransferEngine
from app.services.call_service import CallService
from app.services.transfer_service import TransferService

# ── Singleton: SessionCoordinator + DirectSessionManager ──────────────────────
# SessionCoordinator owns the worker_id, heartbeat tasks, and pub/sub routing.
# DirectSessionManager holds the live in-process session dict.
# Both are created once per worker process and shared across all requests.
_session_coordinator: Optional["SessionCoordinator"] = None   # type: ignore
_direct_session_manager: Optional["DirectSessionManager"] = None  # type: ignore
_mango_transfer_engine: Optional[MangoTransferEngine] = None
_browser_registry: Optional["BrowserSessionRegistry"] = None  # type: ignore


def _get_or_create_session_coordinator() -> "SessionCoordinator":  # type: ignore
    global _session_coordinator
    if _session_coordinator is not None:
        return _session_coordinator

    import uuid as _uuid
    from app.integrations.direct.session_coordinator import SessionCoordinator
    from app.integrations.direct.session_store import (
        InMemorySessionStore,
        RedisSessionStore,
    )

    redis = get_redis()
    if redis is not None:
        store = RedisSessionStore(redis)
        log.info("session_coordinator.using_redis_store")
    else:
        store = InMemorySessionStore()
        log.warning(
            "session_coordinator.using_in_memory_store",
            message=(
                "Redis unavailable — DirectSessionManager falls back to in-process "
                "store.  Sessions will be lost on restart and multi-worker deployment "
                "is unsafe."
            ),
        )

    worker_id = f"worker-{_uuid.uuid4().hex[:12]}"
    _session_coordinator = SessionCoordinator(store=store, worker_id=worker_id)
    log.info("session_coordinator.initialised", worker_id=worker_id)
    return _session_coordinator


def _get_or_create_session_manager() -> "DirectSessionManager":  # type: ignore
    global _direct_session_manager
    if _direct_session_manager is None:
        from app.integrations.direct.session_manager import DirectSessionManager
        coordinator = None if settings.is_testing else _get_or_create_session_coordinator()
        _direct_session_manager = DirectSessionManager(coordinator=coordinator)
    return _direct_session_manager


def get_session_coordinator() -> "SessionCoordinator":  # type: ignore
    """Expose coordinator for startup reconciliation and observability."""
    return _get_or_create_session_coordinator()


def get_direct_session_manager() -> "DirectSessionManager":  # type: ignore
    return _get_or_create_session_manager()


def get_browser_registry() -> "BrowserSessionRegistry":  # type: ignore
    global _browser_registry
    if _browser_registry is None:
        from app.integrations.browser.registry import BrowserSessionRegistry

        _browser_registry = BrowserSessionRegistry()
    return _browser_registry


def _build_voice_provider():
    from app.integrations.voice.stub import StubVoiceProvider

    if settings.elevenlabs_configured:
        from app.integrations.voice.elevenlabs import ElevenLabsClient

        voice = ElevenLabsClient()
        log.info("deps.direct_engine.using_elevenlabs_voice")
    else:
        voice = StubVoiceProvider()

    if (
        settings.is_production
        and settings.direct_voice_strategy in {"tts_primary", "experimental_hybrid"}
        and not settings.elevenlabs_configured
    ):
        log.warning(
            "deps.direct_engine.stub_voice_in_production",
            note=(
                "Voice strategy requires ElevenLabs TTS but it is not configured. "
                "Direct voice calls will fail fast."
            ),
        )
    return voice


# ── Call engine ───────────────────────────────────────────────────────────────

async def get_call_engine() -> AbstractCallEngine:
    """
    Возвращает RoutingCallEngine — выбирает sub-engine по call.mode.

    Существующие тесты не затронуты: они создают CallService(engine=StubEngine())
    напрямую, минуя этот DI.
    """
    from app.integrations.call_engine.router_engine import RoutingCallEngine

    # ── Vapi engine ───────────────────────────────────────────────────────────
    vapi_engine: Optional[AbstractCallEngine] = None
    if settings.vapi_configured:
        from app.integrations.vapi.engine import VapiCallEngine
        vapi_engine = VapiCallEngine()

    # ── Direct (Gemini Live) engine ───────────────────────────────────────────
    direct_engine: Optional[AbstractCallEngine] = None
    browser_engine: Optional[AbstractCallEngine] = None
    if settings.gemini_configured:
        from app.integrations.direct.engine import DirectGeminiEngine
        from app.integrations.browser.engine import BrowserDirectEngine
        from app.integrations.browser.telephony import BrowserTelephonyAdapter
        from app.integrations.telephony.registry import build_default_registry

        # Use provider registry — decoupled from Mango-specific logic.
        # TELEPHONY_PROVIDER="auto" → picks Mango if configured, else Stub.
        # Override with TELEPHONY_PROVIDER="mango"/"twilio"/"stub" in .env.
        _telephony_registry = build_default_registry()
        telephony = _telephony_registry.resolve(settings.telephony_provider)
        voice = _build_voice_provider()

        engine = DirectGeminiEngine(
            session_manager=_get_or_create_session_manager(),
            telephony=telephony,
            voice=voice,
            # session_factory инжектируется позже через set_session_factory()
            # т.к. здесь нет доступа к async_sessionmaker напрямую
            session_factory=None,
        )
        # Lazy session factory: будет установлена при первом initiate_call
        # через _inject_session_factory в RoutingCallEngine
        direct_engine = engine
        browser_engine = BrowserDirectEngine(
            session_manager=_get_or_create_session_manager(),
            telephony=BrowserTelephonyAdapter(get_browser_registry()),
            voice=voice,
            session_factory=None,
        )

    return RoutingCallEngine(
        vapi_engine=vapi_engine,
        direct_engine=direct_engine,
        browser_engine=browser_engine,
        fallback_engine=StubEngine(),
    )


async def get_call_service(
    session: AsyncSession = Depends(get_db),
    engine: AbstractCallEngine = Depends(get_call_engine),
) -> AsyncGenerator[CallService, None]:
    # Инжектировать session_factory в DirectGeminiEngine если нужно
    _maybe_inject_session_factory(engine, session)
    yield CallService(session=session, engine=engine)


def _maybe_inject_session_factory(
    engine: AbstractCallEngine,
    session: AsyncSession,
) -> None:
    """
    Если engine содержит DirectGeminiEngine — установить session_factory.
    Нужно т.к. DirectEventHandler создаёт отдельные DB сессии per-event,
    а не использует request-scoped сессию.
    """
    from app.integrations.call_engine.router_engine import RoutingCallEngine
    from app.integrations.browser.engine import BrowserDirectEngine
    from app.integrations.direct.engine import DirectGeminiEngine

    if not isinstance(engine, RoutingCallEngine):
        return
    # Создаём sessionmaker из того же engine что и текущая сессия
    from sqlalchemy.ext.asyncio import async_sessionmaker

    bind = getattr(session, "bind", None) or session.get_bind()
    factory = async_sessionmaker(bind=bind, expire_on_commit=False)
    for candidate in (engine._direct, getattr(engine, "_browser", None)):
        if candidate is None or not isinstance(candidate, (DirectGeminiEngine, BrowserDirectEngine)):
            continue
        if candidate._session_factory is None:
            candidate.set_session_factory(factory)


# ── Transfer engine ───────────────────────────────────────────────────────────

def get_transfer_engine() -> AbstractTransferEngine:
    """
    Returns the active transfer engine.
    Mango configured: MangoTransferEngine (real manager dial via Mango control-plane)
    Otherwise: StubTransferEngine fallback.
    """
    global _mango_transfer_engine
    if settings.mango_configured:
        if _mango_transfer_engine is None:
            from sqlalchemy.ext.asyncio import async_sessionmaker
            from app.integrations.telephony.mango import MangoTelephonyAdapter
            from app.db.session import engine as db_engine

            session_factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
            _mango_transfer_engine = MangoTransferEngine(
                telephony=MangoTelephonyAdapter(),
                session_factory=session_factory,
                direct_session_manager=_get_or_create_session_manager() if settings.gemini_configured else None,
            )
        return _mango_transfer_engine

    if settings.is_production:
        log.warning(
            "transfer_engine.stub_in_production",
            message="StubTransferEngine is active — warm transfers will NOT dial managers",
        )
    return StubTransferEngine()


async def get_transfer_service(
    session: AsyncSession = Depends(get_db),
    engine: AbstractTransferEngine = Depends(get_transfer_engine),
) -> AsyncGenerator[TransferService, None]:
    yield TransferService(session=session, engine=engine)


# ── Rate limiting / Abuse policy ──────────────────────────────────────────────

async def get_abuse_policy(
    session: AsyncSession = Depends(get_db),
) -> AbusePolicy:
    """
    Returns the abuse policy checker for rate limiting.
    Initializes with Redis client (fail-open if unavailable).
    """
    return AbusePolicy(redis=get_redis(), session=session)

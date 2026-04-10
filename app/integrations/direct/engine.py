"""
DirectGeminiEngine — реализация AbstractCallEngine для Direct mode.

Оркестрирует:
  DirectSessionManager   — lifecycle Gemini Live сессий
  AbstractTelephonyAdapter — телефонный канал (Phase 1: Stub)
  AbstractVoiceProvider   — TTS (Phase 1: Stub)

Маппинг AbstractCallEngine методов:
  initiate_call()    → telephony.connect() + session_manager.create_session()
  stop_call()        → session_manager.terminate_session()
  send_instruction() → session_manager.inject_instruction()
  get_status()       → session.current_status или COMPLETED если нет сессии

Хранение external_id:
  EngineCallResult.external_id = session_id ("{call_id}-direct")
  CallService.create_call() запишет это в call.mango_call_id.

Требования к CallService (одно изменение):
  Если call.mode == DIRECT → result.external_id → call.mango_call_id
  Иначе → call.vapi_call_id (текущее поведение, без изменений)
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.exceptions import EngineError
from app.core.logging import get_logger
from app.integrations.call_engine.base import AbstractCallEngine, EngineCallResult
from app.integrations.direct.session_manager import DirectSessionManager
from app.integrations.telephony.base import AbstractTelephonyAdapter
from app.integrations.voice.base import AbstractVoiceProvider
from app.models.call import Call, CallStatus
from app.services.agent_profile_service import build_agent_runtime_configuration
from app.services.knowledge_base_service import KnowledgeBaseService

log = get_logger(__name__)


class DirectGeminiEngine(AbstractCallEngine):
    """
    Call engine для Direct mode через Google Gemini Live API.
    Production-ready Phase 1.
    """

    def __init__(
        self,
        session_manager: DirectSessionManager,
        telephony: AbstractTelephonyAdapter,
        voice: AbstractVoiceProvider,
        session_factory: Optional[async_sessionmaker] = None,
    ) -> None:
        self._sm = session_manager
        self._telephony = telephony
        self._voice = voice
        # session_factory для DirectEventHandler (сохранение транскрипта)
        # Если не передан — инжектируется позже через set_session_factory()
        self._session_factory = session_factory

    def set_session_factory(self, session_factory: async_sessionmaker) -> None:
        """
        Установить DB session factory.
        Вызывается из deps.py после создания engine.
        Нужно т.к. session_factory не доступна в момент создания singleton engine.
        """
        self._session_factory = session_factory

    # ── AbstractCallEngine ────────────────────────────────────────────────────

    async def initiate_call(self, call: Call) -> EngineCallResult:
        """
        Запустить Direct сессию:
        1. Инициировать телефонный канал (stub: noop)
        2. Создать DirectSession с GeminiLiveClient
        3. Вернуть session_id как external_id → запишется в call.mango_call_id
        """
        if self._session_factory is None:
            raise RuntimeError(
                "DirectGeminiEngine.session_factory не установлен. "
                "Вызови set_session_factory() перед initiate_call()."
            )

        log.info(
            "direct_engine.initiate_call",
            call_id=str(call.id),
            phone=call.phone,
            agent_profile_id=str(call.agent_profile_id) if call.agent_profile_id else None,
        )
        knowledge_context = None
        if self._session_factory is not None and call.agent_profile_id is not None:
            try:
                async with self._session_factory() as runtime_session:
                    kb_service = KnowledgeBaseService(runtime_session)
                    knowledge_context = await kb_service.build_agent_runtime_knowledge_context(
                        call.agent_profile_id
                    )
            except Exception as exc:
                log.warning(
                    "direct_engine.knowledge_context_unavailable",
                    call_id=str(call.id),
                    agent_profile_id=str(call.agent_profile_id)
                    if call.agent_profile_id
                    else None,
                    error=str(exc),
                )

        runtime_agent = build_agent_runtime_configuration(
            getattr(call, "agent_profile", None),
            knowledge_context=knowledge_context,
        )
        try:
            session_id = await self._sm.create_session(
                call_id=call.id,
                phone=call.phone,
                telephony=self._telephony,
                voice=self._voice,
                session_factory=self._session_factory,
                system_prompt=runtime_agent.system_prompt or None,
                initial_greeting_text=runtime_agent.greeting_text,
                voice_strategy_name=runtime_agent.voice_strategy,
            )
        except EngineError:
            raise
        except Exception as exc:
            log.error(
                "direct_engine.initiate_call_failed",
                call_id=str(call.id),
                stage="session_create",
                error=str(exc),
            )
            raise EngineError(
                "Direct call initiation failed",
                detail={"call_id": str(call.id), "stage": "session_create", "error": str(exc)},
            ) from exc
        session = self._sm.get_session(session_id)
        telephony_leg_id = None
        session_mode = "unknown"
        audio_in = False
        audio_out = False
        real_audio_in = False
        real_audio_out = False
        if session and session.telephony_channel:
            telephony_leg_id = session.telephony_channel.provider_leg_id
            session_mode = session.capabilities.mode
            audio_in = session.capabilities.audio_in
            audio_out = session.capabilities.audio_out
            real_audio_in = session.capabilities.real_audio_in
            real_audio_out = session.capabilities.real_audio_out

        log.info(
            "direct_engine.session_started",
            call_id=str(call.id),
            session_id=session_id,
        )

        return EngineCallResult(
            external_id=session_id,
            initial_status=CallStatus.IN_PROGRESS,
            route_used="direct",
            telephony_leg_id=telephony_leg_id,
            metadata={
                "engine": "direct_gemini",
                "session_id": session_id,
                "agent_profile_id": str(runtime_agent.agent_id) if runtime_agent.agent_id else None,
                "agent_profile_name": runtime_agent.name,
                "agent_profile_version": runtime_agent.version,
                "session_mode": session_mode,
                "audio_in": audio_in,
                "audio_out": audio_out,
                "real_audio_in": real_audio_in,
                "real_audio_out": real_audio_out,
                "voice_strategy": (
                    session.voice_state.strategy
                    if session and session.voice_state is not None
                    else "unknown"
                ),
                "primary_voice_path": (
                    session.voice_state.primary_path
                    if session and session.voice_state is not None
                    else "unknown"
                ),
                "active_voice_path": (
                    session.voice_state.active_path
                    if session and session.voice_state is not None
                    else "unknown"
                ),
                "fallback_voice_path": (
                    session.voice_state.fallback_path
                    if session and session.voice_state is not None
                    else None
                ),
                "knowledge_document_count": (
                    len(runtime_agent.knowledge_context.documents)
                    if runtime_agent.knowledge_context is not None
                    else 0
                ),
                "knowledge_categories": (
                    sorted(runtime_agent.knowledge_context.categories.keys())
                    if runtime_agent.knowledge_context is not None
                    else []
                ),
                "company_profile_name": (
                    runtime_agent.company_profile.get("name")
                    if runtime_agent.company_profile is not None
                    else None
                ),
            },
        )

    async def stop_call(self, call: Call) -> None:
        """
        Завершить Direct сессию.
        Idempotent — вызов на уже завершённой сессии безопасен.
        """
        session_id = call.mango_call_id
        if not session_id:
            log.warning(
                "direct_engine.stop_call.no_session_id",
                call_id=str(call.id),
                note="mango_call_id is None — session may not have been started",
            )
            return

        log.info(
            "direct_engine.stop_call",
            call_id=str(call.id),
            session_id=session_id,
        )
        await self._sm.terminate_session(
            session_id,
            final_status=CallStatus.STOPPED,
            stage="stop_call",
            reason="stop requested",
        )

    async def send_instruction(self, call: Call, instruction: str) -> None:
        """
        Инжектировать steering instruction в активную Gemini сессию.

        Instruction попадает в asyncio.Queue → background task →
        GeminiLiveClient.inject_instruction() → ws.send(clientContent).

        Логирование применённых инструкций происходит в session_manager.
        Best-effort: если сессия не найдена — логирует и возвращает.
        """
        session_id = call.mango_call_id
        if not session_id:
            log.warning(
                "direct_engine.send_instruction.no_session_id",
                call_id=str(call.id),
            )
            return

        log.info(
            "direct_engine.send_instruction",
            call_id=str(call.id),
            session_id=session_id,
            preview=instruction[:80],
        )
        await self._sm.inject_instruction(session_id, instruction)

    async def get_status(self, call: Call) -> CallStatus:
        """
        Вернуть текущий статус сессии.
        Если сессия не найдена → COMPLETED (закончилась или не была создана).
        """
        session_id = call.mango_call_id
        if not session_id:
            return call.status

        session = self._sm.get_session(session_id)
        if session is None:
            return call.status

        return session.current_status

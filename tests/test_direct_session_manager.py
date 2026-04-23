"""
Тесты для DirectSessionManager.

7 тестов:
- create_session → сессия появляется в _sessions
- terminate_session → сессия удаляется
- inject_instruction → попадает в instruction_queue
- terminate_session на несуществующей сессии — idempotent
- get_session → None для неизвестного session_id
- terminate_session закрывает audio_bridge (проверка Fix 1 — NameError bug)
- ошибка в bridge.close() не роняет terminate_session

Все тесты используют MockGeminiLiveClient — не открывают реальный WebSocket.
"""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import settings
from app.integrations.direct.session_manager import DirectSession, DirectSessionManager
from app.integrations.telephony.base import TelephonyLegState
from app.integrations.telephony.stub import StubTelephonyAdapter
from app.integrations.voice.stub import StubVoiceProvider
from app.models.call import CallStatus
from tests.conftest import MockGeminiLiveClient


@pytest.fixture
def sm() -> DirectSessionManager:
    return DirectSessionManager()


@pytest.fixture
def mock_session_factory():
    return AsyncMock()


async def _create_session(
    sm: DirectSessionManager,
    session_factory,
    call_id: uuid.UUID = None,
) -> str:
    """Хелпер: создать сессию с MockGeminiLiveClient."""
    cid = call_id or uuid.uuid4()
    telephony = StubTelephonyAdapter()
    voice = StubVoiceProvider()

    # Патчим GeminiLiveClient чтобы не открывать реальный WS
    old_el_enabled = settings.elevenlabs_enabled
    old_el_key = settings.elevenlabs_api_key
    old_el_voice = settings.elevenlabs_voice_id
    old_greeting_enabled = settings.direct_initial_greeting_enabled
    old_voice_strategy = settings.direct_voice_strategy
    try:
        settings.direct_voice_strategy = "tts_primary"
        settings.elevenlabs_enabled = True
        settings.elevenlabs_api_key = "k"
        settings.elevenlabs_voice_id = "v"
        settings.direct_initial_greeting_enabled = False
        with patch(
            "app.integrations.direct.session_manager.GeminiLiveClient",
            new=MockGeminiLiveClient,
        ):
            session_id = await sm.create_session(
                call_id=cid,
                phone="+79991234567",
                telephony=telephony,
                voice=voice,
                session_factory=session_factory,
                system_prompt="Тест",
            )
    finally:
        settings.elevenlabs_enabled = old_el_enabled
        settings.elevenlabs_api_key = old_el_key
        settings.elevenlabs_voice_id = old_el_voice
        settings.direct_initial_greeting_enabled = old_greeting_enabled
        settings.direct_voice_strategy = old_voice_strategy
    # Остановим bg_task немедленно чтобы тест не зависал
    session = sm.get_session(session_id)
    if session and session.bg_task:
        session.bg_task.cancel()
    return session_id


@pytest.mark.anyio
async def test_create_session_appears_in_sessions(mock_session_factory):
    """create_session добавляет сессию в _sessions."""
    sm = DirectSessionManager()
    session_id = await _create_session(sm, mock_session_factory)

    assert sm.get_session(session_id) is not None
    assert sm.active_count() == 1


@pytest.mark.anyio
async def test_terminate_session_removes_from_sessions(mock_session_factory):
    """terminate_session удаляет сессию из _sessions."""
    sm = DirectSessionManager()
    session_id = await _create_session(sm, mock_session_factory)

    assert sm.active_count() == 1
    await sm.terminate_session(session_id)
    assert sm.get_session(session_id) is None
    assert sm.active_count() == 0


@pytest.mark.anyio
async def test_inject_instruction_queues_instruction(mock_session_factory):
    """inject_instruction кладёт инструкцию в instruction_queue сессии."""
    sm = DirectSessionManager()
    session_id = await _create_session(sm, mock_session_factory)

    await sm.inject_instruction(session_id, "Уточни бюджет")

    session = sm.get_session(session_id)
    assert not session.instruction_queue.empty()
    queued = session.instruction_queue.get_nowait()
    assert queued == "Уточни бюджет"

    # Cleanup
    await sm.terminate_session(session_id)


@pytest.mark.anyio
async def test_terminate_nonexistent_session_is_idempotent():
    """terminate_session на несуществующей сессии не падает."""
    sm = DirectSessionManager()
    await sm.terminate_session("nonexistent-session-id")  # не должен упасть
    assert sm.active_count() == 0


@pytest.mark.anyio
async def test_get_session_returns_none_for_unknown():
    """get_session возвращает None для неизвестного session_id."""
    sm = DirectSessionManager()
    result = sm.get_session("totally-unknown-id")
    assert result is None


@pytest.mark.anyio
async def test_terminate_session_closes_audio_bridge(mock_session_factory):
    """terminate_session должен вызвать audio_bridge.close() — регрессия Fix 1 (NameError bug)."""
    sm = DirectSessionManager()
    session_id = await _create_session(sm, mock_session_factory)

    session = sm.get_session(session_id)
    bridge = session.audio_bridge
    # SilenceAudioBridge открыт после create_session (attach_audio_bridge вызывает open())
    assert bridge is not None
    assert bridge.is_open

    await sm.terminate_session(session_id)

    assert not bridge.is_open
    assert sm.active_count() == 0


@pytest.mark.anyio
async def test_terminate_session_bridge_close_error_does_not_crash(mock_session_factory):
    """Ошибка при bridge.close() не должна ронять terminate_session."""
    sm = DirectSessionManager()
    session_id = await _create_session(sm, mock_session_factory)

    session = sm.get_session(session_id)
    bad_bridge = AsyncMock()
    bad_bridge.close.side_effect = RuntimeError("bridge exploded")
    bad_bridge.__bool__ = lambda self: True
    session.audio_bridge = bad_bridge

    # Не должен упасть
    await sm.terminate_session(session_id)
    assert sm.active_count() == 0


@pytest.mark.anyio
async def test_terminate_session_terminates_telephony_leg(mock_session_factory):
    """Direct session shutdown must also terminate the provider leg, not only the AI session."""
    sm = DirectSessionManager()
    telephony = StubTelephonyAdapter()
    voice = StubVoiceProvider()

    old_el_enabled = settings.elevenlabs_enabled
    old_el_key = settings.elevenlabs_api_key
    old_el_voice = settings.elevenlabs_voice_id
    old_greeting_enabled = settings.direct_initial_greeting_enabled
    old_voice_strategy = settings.direct_voice_strategy
    try:
        settings.direct_voice_strategy = "tts_primary"
        settings.elevenlabs_enabled = True
        settings.elevenlabs_api_key = "k"
        settings.elevenlabs_voice_id = "v"
        settings.direct_initial_greeting_enabled = False
        with patch(
            "app.integrations.direct.session_manager.GeminiLiveClient",
            new=MockGeminiLiveClient,
        ):
            session_id = await sm.create_session(
                call_id=uuid.uuid4(),
                phone="+79991234567",
                telephony=telephony,
                voice=voice,
                session_factory=mock_session_factory,
                system_prompt="Тест",
            )
    finally:
        settings.elevenlabs_enabled = old_el_enabled
        settings.elevenlabs_api_key = old_el_key
        settings.elevenlabs_voice_id = old_el_voice
        settings.direct_initial_greeting_enabled = old_greeting_enabled
        settings.direct_voice_strategy = old_voice_strategy

    session = sm.get_session(session_id)
    leg_id = session.telephony_channel.provider_leg_id
    await sm.terminate_session(session_id)
    assert await telephony.get_leg_state(leg_id) == TelephonyLegState.TERMINATED


@pytest.mark.anyio
async def test_terminate_session_still_finalizes_when_gemini_close_fails(mock_session_factory):
    sm = DirectSessionManager()
    session_id = await _create_session(sm, mock_session_factory)

    session = sm.get_session(session_id)
    assert session is not None
    assert session.event_handler is not None
    assert session.gemini_client is not None

    session.event_handler.flush = AsyncMock(return_value=None)  # type: ignore[method-assign]
    session.event_handler.finalize_call = AsyncMock(return_value=None)  # type: ignore[method-assign]
    session.gemini_client.close = AsyncMock(side_effect=RuntimeError("close exploded"))  # type: ignore[method-assign]

    await sm.terminate_session(
        session_id,
        final_status=CallStatus.FAILED,
        stage="test_close_failure",
        reason="forced",
    )

    session.event_handler.finalize_call.assert_awaited_once_with(
        CallStatus.FAILED,
        stage="test_close_failure",
        reason="forced",
        disconnect_reason=None,
        last_error=None,
    )


@pytest.mark.anyio
async def test_telephony_leg_monitor_terminates_failed_session(mock_session_factory):
    """If the provider leg dies underneath the session, DirectSessionManager must clean up."""
    sm = DirectSessionManager()
    telephony = StubTelephonyAdapter()
    voice = StubVoiceProvider()

    old_el_enabled = settings.elevenlabs_enabled
    old_el_key = settings.elevenlabs_api_key
    old_el_voice = settings.elevenlabs_voice_id
    old_greeting_enabled = settings.direct_initial_greeting_enabled
    old_voice_strategy = settings.direct_voice_strategy
    try:
        settings.direct_voice_strategy = "tts_primary"
        settings.elevenlabs_enabled = True
        settings.elevenlabs_api_key = "k"
        settings.elevenlabs_voice_id = "v"
        settings.direct_initial_greeting_enabled = False
        with patch(
            "app.integrations.direct.session_manager.GeminiLiveClient",
            new=MockGeminiLiveClient,
        ):
            session_id = await sm.create_session(
                call_id=uuid.uuid4(),
                phone="+79991234567",
                telephony=telephony,
                voice=voice,
                session_factory=mock_session_factory,
                system_prompt="Тест",
            )
    finally:
        settings.elevenlabs_enabled = old_el_enabled
        settings.elevenlabs_api_key = old_el_key
        settings.elevenlabs_voice_id = old_el_voice
        settings.direct_initial_greeting_enabled = old_greeting_enabled
        settings.direct_voice_strategy = old_voice_strategy

    session = sm.get_session(session_id)
    assert session is not None
    await asyncio.sleep(0.05)
    assert session.leg_monitor_task is not None
    leg_id = session.telephony_channel.provider_leg_id

    await telephony.terminate_leg(leg_id)

    async def _wait_until_gone() -> None:
        while sm.get_session(session_id) is not None:
            await asyncio.sleep(0.05)

    await asyncio.wait_for(_wait_until_gone(), timeout=2.0)
    assert sm.active_count() == 0


@pytest.mark.anyio
async def test_terminate_session_cancels_leg_monitor(mock_session_factory):
    sm = DirectSessionManager()
    telephony = StubTelephonyAdapter()
    voice = StubVoiceProvider()

    old_el_enabled = settings.elevenlabs_enabled
    old_el_key = settings.elevenlabs_api_key
    old_el_voice = settings.elevenlabs_voice_id
    old_greeting_enabled = settings.direct_initial_greeting_enabled
    old_voice_strategy = settings.direct_voice_strategy
    try:
        settings.direct_voice_strategy = "tts_primary"
        settings.elevenlabs_enabled = True
        settings.elevenlabs_api_key = "k"
        settings.elevenlabs_voice_id = "v"
        settings.direct_initial_greeting_enabled = False
        with patch(
            "app.integrations.direct.session_manager.GeminiLiveClient",
            new=MockGeminiLiveClient,
        ):
            session_id = await sm.create_session(
                call_id=uuid.uuid4(),
                phone="+79991234567",
                telephony=telephony,
                voice=voice,
                session_factory=mock_session_factory,
                system_prompt="Тест",
            )
    finally:
        settings.elevenlabs_enabled = old_el_enabled
        settings.elevenlabs_api_key = old_el_key
        settings.elevenlabs_voice_id = old_el_voice
        settings.direct_initial_greeting_enabled = old_greeting_enabled
        settings.direct_voice_strategy = old_voice_strategy

    session = sm.get_session(session_id)
    assert session is not None
    await asyncio.sleep(0.05)
    assert session.leg_monitor_task is not None

    await sm.terminate_session(session_id)
    assert session.leg_monitor_task.done()

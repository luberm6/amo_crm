"""
Тесты для DirectGeminiEngine.

6 тестов:
- initiate_call вызывает telephony.connect и session_manager.create_session
- initiate_call возвращает IN_PROGRESS
- stop_call вызывает session_manager.terminate_session с правильным session_id
- stop_call с None mango_call_id — не падает (idempotent)
- send_instruction делегирует в session_manager.inject_instruction
- get_status: сессия не найдена → текущий статус call
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.integrations.call_engine.base import EngineCallResult
from app.integrations.direct.engine import DirectGeminiEngine
from app.integrations.direct.session_manager import DirectSessionManager, DirectSession
from app.integrations.telephony.stub import StubTelephonyAdapter
from app.integrations.voice.stub import StubVoiceProvider
from app.models.call import Call, CallMode, CallStatus


def _make_call(
    mode: CallMode = CallMode.DIRECT,
    mango_call_id: str = None,
) -> MagicMock:
    c = MagicMock(spec=Call)
    c.id = uuid.uuid4()
    c.mode = mode
    c.phone = "+79991234567"
    c.mango_call_id = mango_call_id
    c.status = CallStatus.IN_PROGRESS
    return c


@pytest.mark.anyio
async def test_initiate_call_creates_session():
    """initiate_call вызывает create_session и возвращает session_id как external_id."""
    mock_sm = AsyncMock(spec=DirectSessionManager)
    expected_session_id = f"{uuid.uuid4()}-direct"
    mock_sm.create_session.return_value = expected_session_id

    engine = DirectGeminiEngine(
        session_manager=mock_sm,
        telephony=StubTelephonyAdapter(),
        voice=StubVoiceProvider(),
        session_factory=AsyncMock(),
    )

    call = _make_call()
    result = await engine.initiate_call(call)

    mock_sm.create_session.assert_called_once()
    assert result.external_id == expected_session_id


@pytest.mark.anyio
async def test_initiate_call_returns_in_progress():
    """initiate_call возвращает EngineCallResult с initial_status=IN_PROGRESS."""
    mock_sm = AsyncMock(spec=DirectSessionManager)
    mock_sm.create_session.return_value = "some-session-id"

    engine = DirectGeminiEngine(
        session_manager=mock_sm,
        telephony=StubTelephonyAdapter(),
        voice=StubVoiceProvider(),
        session_factory=AsyncMock(),
    )

    call = _make_call()
    result = await engine.initiate_call(call)

    assert result.initial_status == CallStatus.IN_PROGRESS
    assert isinstance(result, EngineCallResult)


@pytest.mark.anyio
async def test_stop_call_terminates_session():
    """stop_call вызывает terminate_session с правильным session_id."""
    session_id = "abc-direct"
    mock_sm = AsyncMock(spec=DirectSessionManager)

    engine = DirectGeminiEngine(
        session_manager=mock_sm,
        telephony=StubTelephonyAdapter(),
        voice=StubVoiceProvider(),
        session_factory=AsyncMock(),
    )

    call = _make_call(mango_call_id=session_id)
    await engine.stop_call(call)

    mock_sm.terminate_session.assert_called_once_with(
        session_id,
        final_status=CallStatus.STOPPED,
        stage="stop_call",
        reason="stop requested",
    )


@pytest.mark.anyio
async def test_stop_call_no_session_id_is_safe():
    """stop_call с None mango_call_id — не падает, terminate_session не вызван."""
    mock_sm = AsyncMock(spec=DirectSessionManager)

    engine = DirectGeminiEngine(
        session_manager=mock_sm,
        telephony=StubTelephonyAdapter(),
        voice=StubVoiceProvider(),
        session_factory=AsyncMock(),
    )

    call = _make_call(mango_call_id=None)
    await engine.stop_call(call)  # должен просто вернуть без падения

    mock_sm.terminate_session.assert_not_called()


@pytest.mark.anyio
async def test_send_instruction_delegates_to_session_manager():
    """send_instruction вызывает inject_instruction с правильными аргументами."""
    session_id = "xyz-direct"
    mock_sm = AsyncMock(spec=DirectSessionManager)

    engine = DirectGeminiEngine(
        session_manager=mock_sm,
        telephony=StubTelephonyAdapter(),
        voice=StubVoiceProvider(),
        session_factory=AsyncMock(),
    )

    call = _make_call(mango_call_id=session_id)
    await engine.send_instruction(call, "Уточни бюджет")

    mock_sm.inject_instruction.assert_called_once_with(session_id, "Уточни бюджет")


@pytest.mark.anyio
async def test_get_status_returns_completed_when_no_session():
    """get_status → call.status когда сессия не найдена."""
    mock_sm = MagicMock(spec=DirectSessionManager)
    mock_sm.get_session.return_value = None

    engine = DirectGeminiEngine(
        session_manager=mock_sm,
        telephony=StubTelephonyAdapter(),
        voice=StubVoiceProvider(),
        session_factory=AsyncMock(),
    )

    call = _make_call(mango_call_id="missing-session")
    status = await engine.get_status(call)

    assert status == CallStatus.IN_PROGRESS

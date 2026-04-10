"""
Тесты для DirectEventHandler.

4 теста:
- on_text callback → TranscriptEntry создаётся в SQLite
- role mapping: "user" → USER, "assistant" → ASSISTANT
- пустой text → TranscriptEntry НЕ создаётся
- ошибка DB → не крашит (logged, swallowed)
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.integrations.direct.event_handler import DirectEventHandler
from app.models.call import Call, CallMode, CallStatus
from app.models.transcript import TranscriptEntry, TranscriptRole
from app.repositories.call_repo import CallRepository
from app.repositories.transcript_repo import TranscriptRepository


async def _make_call(session: AsyncSession) -> Call:
    call = Call(phone="+79991234567", mode=CallMode.DIRECT, status=CallStatus.IN_PROGRESS)
    repo = CallRepository(Call, session)
    return await repo.save(call)


@pytest.mark.anyio
async def test_on_text_saves_transcript_entry(session: AsyncSession, test_engine):
    """on_text callback → TranscriptEntry создаётся в БД."""
    call = await _make_call(session)
    factory = async_sessionmaker(bind=test_engine, expire_on_commit=False)

    handler = DirectEventHandler(call_id=call.id, session_factory=factory)
    callback = handler.make_text_callback()

    # Вызываем callback (синхронный) — внутри создаётся asyncio.Task
    callback("assistant", "Здравствуйте! Как я могу помочь?")

    # Ждём завершения всех pending tasks
    await handler.flush()

    # Проверяем через отдельную сессию (flush записал в committed state)
    async with factory() as check_session:
        repo = TranscriptRepository(TranscriptEntry, check_session)
        entries = await repo.get_by_call(call.id)

    assert len(entries) == 1
    assert entries[0].text == "Здравствуйте! Как я могу помочь?"
    assert entries[0].role == TranscriptRole.ASSISTANT


@pytest.mark.anyio
async def test_role_mapping_user(session: AsyncSession, test_engine):
    """role='user' маппируется в TranscriptRole.USER."""
    call = await _make_call(session)
    factory = async_sessionmaker(bind=test_engine, expire_on_commit=False)

    handler = DirectEventHandler(call_id=call.id, session_factory=factory)
    callback = handler.make_text_callback()

    callback("user", "Да, меня интересует покупка")
    await handler.flush()

    async with factory() as check_session:
        repo = TranscriptRepository(TranscriptEntry, check_session)
        entries = await repo.get_by_call(call.id)

    assert len(entries) == 1
    assert entries[0].role == TranscriptRole.USER


@pytest.mark.anyio
async def test_empty_text_not_saved(session: AsyncSession, test_engine):
    """Пустой или whitespace-only text → запись НЕ создаётся."""
    call = await _make_call(session)
    factory = async_sessionmaker(bind=test_engine, expire_on_commit=False)

    handler = DirectEventHandler(call_id=call.id, session_factory=factory)
    callback = handler.make_text_callback()

    callback("assistant", "   ")  # только пробелы
    callback("assistant", "")     # пустая строка
    await handler.flush()

    async with factory() as check_session:
        repo = TranscriptRepository(TranscriptEntry, check_session)
        entries = await repo.get_by_call(call.id)

    assert len(entries) == 0


@pytest.mark.anyio
async def test_db_error_does_not_crash(session: AsyncSession, test_engine):
    """Ошибка сохранения в DB не вызывает исключение — logged and swallowed."""
    from unittest.mock import AsyncMock, patch

    factory = async_sessionmaker(bind=test_engine, expire_on_commit=False)
    call_id = uuid.uuid4()  # несуществующий call_id → FK error при вставке

    handler = DirectEventHandler(call_id=call_id, session_factory=factory)
    callback = handler.make_text_callback()

    # Вызываем с несуществующим call_id — FK constraint нарушится в SQLite
    # Но исключение должно быть поглощено внутри _save_transcript
    callback("assistant", "Тест с ошибкой FK")

    # flush() должен завершиться без исключения
    await handler.flush()
    # Дошли сюда — тест прошёл (ошибка не propagated)

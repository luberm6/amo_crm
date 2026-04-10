"""
DirectEventHandler — сохраняет события Gemini Live в TranscriptEntry.

Используется как callback фабрика для GeminiLiveClient:
  handler = DirectEventHandler(call_id, session_factory)
  client = GeminiLiveClient(on_text=handler.make_text_callback(), ...)

Каждая запись транскрипта сохраняется через тот же TranscriptRepository
что и Vapi mode — единый формат в таблице transcript_entries.

Дизайн:
  - make_text_callback() возвращает синхронный callable (как требует GeminiLiveClient)
  - Внутри создаётся asyncio.Task — fire-and-forget с логированием ошибок
  - DB сессия открывается/закрывается per-event (не держим долгоживущую сессию)
  - pending_tasks() позволяет дождаться всех задач при завершении сессии
"""
from __future__ import annotations

import asyncio
import datetime
import uuid
from typing import Callable, List

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.models.call import TERMINAL_STATUSES, Call, CallStatus
from app.models.transcript import TranscriptEntry, TranscriptRole
from app.repositories.call_repo import CallRepository
from app.repositories.transcript_repo import TranscriptRepository

log = get_logger(__name__)

_ROLE_MAP = {
    "assistant": TranscriptRole.ASSISTANT,
    "user": TranscriptRole.USER,
    "system": TranscriptRole.SYSTEM,
    "tool": TranscriptRole.TOOL,
}


class DirectEventHandler:
    """
    Сохраняет реплики из Gemini Live сессии в TranscriptEntry.

    Переиспользует TranscriptRepository.append() — те же методы что Vapi.
    """

    def __init__(
        self,
        call_id: uuid.UUID,
        session_factory: async_sessionmaker,
    ) -> None:
        self._call_id = call_id
        self._session_factory = session_factory
        # Список pending asyncio tasks — ждём в flush() при завершении
        self._pending: List[asyncio.Task] = []

    def make_text_callback(self) -> Callable[[str, str], None]:
        """
        Вернуть синхронный callable для GeminiLiveClient.on_text.

        Создаёт asyncio.Task для каждой реплики — не блокирует recv loop.
        """
        def on_text(role: str, text: str) -> None:
            task = asyncio.get_event_loop().create_task(
                self._save_transcript(role, text)
            )
            self._pending.append(task)
            # Очищаем завершённые задачи чтобы список не рос бесконечно
            self._pending = [t for t in self._pending if not t.done()]

        return on_text

    async def flush(self, timeout: float = 3.0) -> None:
        """
        Дождаться всех pending transcript задач.
        Вызывается из DirectSessionManager.terminate_session()
        чтобы не потерять последние реплики разговора.
        """
        if not self._pending:
            return
        active = [t for t in self._pending if not t.done()]
        if active:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*active, return_exceptions=True),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                log.warning(
                    "direct_event_handler.flush_timeout",
                    call_id=str(self._call_id),
                    pending_count=len(active),
                )
        self._pending.clear()

    async def finalize_call(self, final_status: CallStatus) -> None:
        """
        Persist call termination to DB: status, completed_at, simple summary.
        Called by DirectSessionManager.terminate_session() after flush().
        Errors are caught and logged — must not crash the cleanup flow.
        """
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    call_repo = CallRepository(Call, session)
                    call = await call_repo.get(self._call_id)
                    if call is None or call.status in TERMINAL_STATUSES:
                        return  # Already terminal
                    # Build simple summary from transcript entries
                    t_repo = TranscriptRepository(TranscriptEntry, session)
                    entries = await t_repo.get_by_call(self._call_id)
                    if entries:
                        from app.services.summary_service import SummaryService
                        _svc = SummaryService()
                        _call_summary = _svc.generate_summary(entries)
                        call.summary = _call_summary.as_text()
                        call.sentiment = _call_summary.sentiment
                    call.status = final_status
                    call.completed_at = datetime.datetime.now(
                        datetime.timezone.utc
                    )
        except Exception as exc:
            log.error(
                "direct_event.finalize_call_failed",
                call_id=str(self._call_id),
                error=str(exc),
            )

    # ── Private ───────────────────────────────────────────────────────────────

    async def _save_transcript(self, role: str, text: str) -> None:
        """Сохранить одну реплику в TranscriptEntry через отдельную DB сессию."""
        text = text.strip()
        if not text:
            return

        mapped_role = _ROLE_MAP.get(role, TranscriptRole.ASSISTANT)

        try:
            async with self._session_factory() as session:
                async with session.begin():
                    repo = TranscriptRepository(TranscriptEntry, session)
                    await repo.append(
                        call_id=self._call_id,
                        role=mapped_role,
                        text=text,
                        raw_payload={"source": "gemini_live", "role": role},
                    )
            log.debug(
                "direct_event.transcript_saved",
                call_id=str(self._call_id),
                role=role,
                text_preview=text[:60],
            )
        except Exception as exc:
            # Ошибка сохранения транскрипта не должна ронять сессию
            log.exception(
                "direct_event.transcript_save_failed",
                call_id=str(self._call_id),
                role=role,
                error=str(exc),
            )

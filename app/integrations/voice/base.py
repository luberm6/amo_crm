"""
AbstractVoiceProvider — интерфейс TTS провайдера для Direct mode.

Реализации:
  StubVoiceProvider    — dev/test, возвращает тишину
  ElevenLabsClient     — production TTS с кастомным голосом [Phase 2]

Примечание об архитектуре:
  При использовании Gemini AUDIO modality (Phase 2) этот класс не задействован —
  Gemini сам генерирует речь и передаёт PCM через audio_stream.
  ElevenLabs нужен только при Gemini TEXT modality + отдельный TTS пайплайн.

Контракт:
  synthesize()          — blocking: вернуть полный PCM ответ
  synthesize_streaming() — streaming: async generator PCM chunks
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional


class AbstractVoiceProvider(ABC):
    """
    TTS абстракция для Direct mode.

    Входные данные: текст (из Gemini TEXT modality или steering).
    Выходные данные: PCM bytes (16kHz mono 16bit) для передачи в telephony.
    """

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        voice_id: Optional[str] = None,
    ) -> bytes:
        """
        Синтезировать текст в PCM аудио (полный ответ).

        voice_id — переопределить дефолтный голос. Если None — использовать
        голос из settings.elevenlabs_voice_id.
        Возвращает PCM bytes (16kHz mono 16bit).
        """
        ...

    @abstractmethod
    async def synthesize_streaming(
        self,
        text: str,
        voice_id: Optional[str] = None,
    ) -> AsyncIterator[bytes]:
        """
        Streaming TTS: yield PCM chunks по мере генерации.

        Для низколатентной передачи в телефонию — первый chunk приходит
        раньше чем весь текст обработан.
        """
        ...

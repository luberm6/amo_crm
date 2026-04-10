"""
StubVoiceProvider — dev/test TTS реализация.

Возвращает тишину вместо реального синтеза речи.
Используется в Direct mode Phase 1 когда ElevenLabs не настроен
или когда Gemini работает в TEXT modality без отдельного TTS.
"""
from __future__ import annotations

from typing import AsyncIterator, Optional

from app.core.logging import get_logger
from app.integrations.voice.base import AbstractVoiceProvider

log = get_logger(__name__)

# 100ms тишины @ 16kHz mono 16bit = 16000 * 2 * 0.1 = 3200 bytes
_SILENCE_100MS = b"\x00" * 3200
# 20ms chunk для streaming
_SILENCE_20MS = b"\x00" * 640


class StubVoiceProvider(AbstractVoiceProvider):
    """
    Stub TTS: логирует текст, возвращает тишину.
    Не вызывает внешних API.
    """

    async def synthesize(
        self,
        text: str,
        voice_id: Optional[str] = None,
    ) -> bytes:
        log.info(
            "stub_voice.synthesize",
            text_preview=text[:60] if text else "",
            char_count=len(text),
            note="stub returning silence",
        )
        return _SILENCE_100MS

    async def synthesize_streaming(
        self,
        text: str,
        voice_id: Optional[str] = None,
    ) -> AsyncIterator[bytes]:
        log.info(
            "stub_voice.synthesize_streaming",
            text_preview=text[:60] if text else "",
            note="stub returning single silence chunk",
        )
        yield _SILENCE_20MS

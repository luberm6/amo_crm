"""
ElevenLabsClient — TTS провайдер через ElevenLabs API.

STUB Phase 1: структура и интерфейс готовы, реальные API вызовы закомментированы.
Раскомментировать в Phase 2 когда понадобится кастомный голос.

Документация API: https://elevenlabs.io/docs/api-reference/text-to-speech

ENV переменные:
  ELEVENLABS_API_KEY    — API ключ из elevenlabs.io → Profile → API Key
  ELEVENLABS_VOICE_ID   — ID голоса из Library или My Voices
  ELEVENLABS_ENABLED    — true/false (по умолчанию false)

Выбор voice_id:
  Не хардкодить! Передавать через settings.elevenlabs_voice_id
  или через параметр voice_id в методе.
"""
from __future__ import annotations

from typing import AsyncIterator, Optional

import httpx

from app.core.config import settings
from app.core.exceptions import EngineError
from app.core.logging import get_logger
from app.integrations.voice.base import AbstractVoiceProvider

log = get_logger(__name__)

_BASE_URL = "https://api.elevenlabs.io"
_TIMEOUT = 30.0


class ElevenLabsClient(AbstractVoiceProvider):
    """
    ElevenLabs TTS — STUB Phase 1.

    Phase 1: synthesize() и synthesize_streaming() возвращают тишину.
             Структура готова для Phase 2.
    Phase 2: Раскомментировать блоки с httpx запросами.
    """

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(
            base_url=_BASE_URL,
            headers={
                "xi-api-key": settings.elevenlabs_api_key,
                "Content-Type": "application/json",
            },
            timeout=_TIMEOUT,
        )

    def _resolve_voice(self, voice_id: Optional[str]) -> str:
        return voice_id or settings.elevenlabs_voice_id

    async def synthesize(
        self,
        text: str,
        voice_id: Optional[str] = None,
    ) -> bytes:
        """
        Synthesize text to PCM using ElevenLabs API.
        Returns 16kHz mono 16bit PCM bytes.
        """
        vid = self._resolve_voice(voice_id)
        try:
            resp = await self._http.post(
                f"/v1/text-to-speech/{vid}",
                json={
                    "text": text,
                    "model_id": "eleven_multilingual_v2",
                    "output_format": "pcm_16000",
                },
            )
        except httpx.RequestError as exc:
            raise EngineError(f"ElevenLabs unreachable: {exc}") from exc
        if resp.status_code >= 400:
            raise EngineError(f"ElevenLabs error {resp.status_code}: {resp.text}")
        return resp.content

    async def synthesize_streaming(
        self,
        text: str,
        voice_id: Optional[str] = None,
    ) -> AsyncIterator[bytes]:
        """
        Stream-synthesize text to PCM using ElevenLabs API.
        Yields 640-byte chunks (20ms @ 16kHz).
        """
        vid = self._resolve_voice(voice_id)
        try:
            async with self._http.stream(
                "POST",
                f"/v1/text-to-speech/{vid}/stream",
                json={
                    "text": text,
                    "model_id": "eleven_multilingual_v2",
                    "output_format": "pcm_16000",
                },
            ) as resp:
                if resp.status_code >= 400:
                    raise EngineError(
                        f"ElevenLabs error {resp.status_code}: {resp.text}"
                    )
                async for chunk in resp.aiter_bytes(chunk_size=640):
                    yield chunk
        except httpx.RequestError as exc:
            raise EngineError(f"ElevenLabs unreachable: {exc}") from exc

    async def close(self) -> None:
        await self._http.aclose()

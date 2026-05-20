"""
Yandex SpeechKit TTS provider (v1 REST API).

Yandex SpeechKit delivers high-quality Russian TTS with natural intonation.
Latency: ~300–600 ms for short phrases. Streaming not supported in v1 REST API.

API reference: https://cloud.yandex.ru/docs/speechkit/tts/request
Voices:        https://cloud.yandex.ru/docs/speechkit/tts/voices

Required env vars:
  YANDEX_SPEECHKIT_API_KEY   — IAM API key from Yandex Cloud console
  YANDEX_SPEECHKIT_FOLDER_ID — Cloud folder ID (optional for API key auth)
  YANDEX_SPEECHKIT_VOICE     — Default voice (e.g. "alena")
  YANDEX_SPEECHKIT_EMOTION   — Default emotion (e.g. "good")

Voice ID format accepted by this provider: "voice_name" or "voice_name:emotion"
  Examples: "alena", "alena:good", "filipp:neutral", "zahar:friendly"
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

import httpx

from app.core.config import settings
from app.core.exceptions import EngineError
from app.core.logging import get_logger
from app.integrations.voice.base import AbstractVoiceProvider

log = get_logger(__name__)

_BASE_URL = "https://tts.api.cloud.yandex.net"
_TTS_ENDPOINT = "/speech/v1/tts:synthesize"
_TIMEOUT = 20.0
_OUTPUT_FORMAT = "lpcm"          # raw PCM16LE, no header
_OUTPUT_SAMPLE_RATE = 16000
_VALIDATION_TEXT = "Тест голоса"
_MAX_ERROR_BODY_PREVIEW = 400

# All available Yandex SpeechKit v1 voices (ru-RU)
VOICES: dict[str, dict] = {
    "alena":     {"gender": "female", "desc": "Алёна — нейтральный"},
    "filipp":    {"gender": "male",   "desc": "Филипп — нейтральный"},
    "ermil":     {"gender": "male",   "desc": "Эрмил — нейтральный"},
    "jane":      {"gender": "female", "desc": "Джейн — эмоциональный"},
    "omazh":     {"gender": "female", "desc": "Омаж — нейтральный"},
    "zahar":     {"gender": "male",   "desc": "Захар — нейтральный"},
    "dasha":     {"gender": "female", "desc": "Даша — дружелюбный"},
    "julia":     {"gender": "female", "desc": "Юлия — нейтральный"},
    "lera":      {"gender": "female", "desc": "Лера — нейтральный"},
    "masha":     {"gender": "female", "desc": "Маша — нейтральный"},
    "marina":    {"gender": "female", "desc": "Марина — нейтральный"},
    "alexander": {"gender": "male",   "desc": "Александр — нейтральный"},
    "kirill":    {"gender": "male",   "desc": "Кирилл — нейтральный"},
    "anton":     {"gender": "male",   "desc": "Антон — нейтральный"},
}

EMOTIONS: dict[str, list[str]] = {
    "alena":  ["neutral", "good"],
    "filipp": ["neutral"],
    "ermil":  ["neutral", "good"],
    "jane":   ["neutral", "good", "evil"],
    "omazh":  ["neutral", "evil"],
    "zahar":  ["neutral", "good"],
    "dasha":  ["neutral", "friendly", "strict"],
    "julia":  ["neutral", "strict"],
    "lera":   ["neutral", "friendly", "strict"],
    "masha":  ["neutral", "friendly", "strict"],
    "marina": ["neutral", "whisper", "friendly"],
    "alexander": ["neutral"],
    "kirill": ["neutral", "friendly", "strict"],
    "anton":  ["neutral"],
}


def _parse_voice_id(voice_id: str) -> tuple[str, str]:
    """Parse 'voice_name:emotion' or 'voice_name' → (voice, emotion)."""
    if ":" in voice_id:
        parts = voice_id.split(":", 1)
        return parts[0].strip(), parts[1].strip()
    return voice_id.strip(), "neutral"


def _mask(value: str) -> Optional[str]:
    if not value:
        return None
    return value[:4] + "..." + value[-4:] if len(value) > 8 else "***"


@dataclass(frozen=True)
class YandexResolvedConfig:
    api_key: str
    folder_id: str
    voice: str
    emotion: str
    config_source: str
    enabled: bool

    @property
    def api_key_masked(self) -> Optional[str]:
        return _mask(self.api_key)


class YandexSpeechKitClient(AbstractVoiceProvider):
    """
    Yandex SpeechKit v1 REST TTS client.

    voice_id format: "voice_name" or "voice_name:emotion"
    E.g. "alena:good", "filipp", "zahar:friendly"
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        folder_id: Optional[str] = None,
        default_voice: Optional[str] = None,
        default_emotion: Optional[str] = None,
        enabled: Optional[bool] = None,
        config_source: str = "env",
        base_url: str = _BASE_URL,
        timeout: float = _TIMEOUT,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self._explicit_api_key = (api_key or "").strip()
        self._explicit_folder_id = (folder_id or "").strip()
        self._explicit_voice = (default_voice or "").strip()
        self._explicit_emotion = (default_emotion or "").strip()
        self._explicit_enabled = enabled
        self._config_source = config_source
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport

    def runtime_diagnostics(self) -> dict[str, Any]:
        try:
            config = self._resolve_config(None)
        except EngineError as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            return {
                "provider": "yandex_speechkit",
                "config_source": self._config_source,
                "api_key_set": bool(self._explicit_api_key or settings.yandex_speechkit_api_key),
                "enabled": self._explicit_enabled if self._explicit_enabled is not None else settings.yandex_speechkit_enabled,
                "stage": detail.get("stage", "provider_resolve"),
            }
        return {
            "provider": "yandex_speechkit",
            "config_source": config.config_source,
            "api_key_masked": config.api_key_masked,
            "voice": config.voice,
            "emotion": config.emotion,
            "enabled": config.enabled,
        }

    async def validate_tts_contract(self, text: str = _VALIDATION_TEXT) -> bytes:
        return await self.synthesize(text)

    async def synthesize(
        self,
        text: str,
        voice_id: Optional[str] = None,
    ) -> bytes:
        config = self._resolve_config(voice_id)
        data = self._build_form(text, config)

        log.info(
            "yandex_speechkit.request_started",
            voice=config.voice,
            emotion=config.emotion,
            text_chars=len(text),
        )

        try:
            async with self._make_client(config) as client:
                resp = await client.post(_TTS_ENDPOINT, data=data)
        except httpx.RequestError as exc:
            raise self._error("Yandex SpeechKit request failed", "http_request", config, {"error": str(exc)}) from exc

        if resp.status_code >= 400:
            raise self._error(
                f"Yandex SpeechKit returned HTTP {resp.status_code}",
                "http_request",
                config,
                {"http_status": resp.status_code, "body_preview": resp.text[:_MAX_ERROR_BODY_PREVIEW]},
            )

        pcm = resp.content
        log.info("yandex_speechkit.request_completed", byte_length=len(pcm), voice=config.voice)
        return pcm

    async def synthesize_streaming(
        self,
        text: str,
        voice_id: Optional[str] = None,
    ) -> AsyncIterator[bytes]:
        # Yandex v1 REST does not support streaming — return in one chunk
        pcm = await self.synthesize(text, voice_id)
        yield pcm

    # ── Private ────────────────────────────────────────────────────────────────

    def _resolve_config(self, voice_id_override: Optional[str]) -> YandexResolvedConfig:
        api_key = self._explicit_api_key or (settings.yandex_speechkit_api_key or "").strip()
        folder_id = self._explicit_folder_id or (settings.yandex_speechkit_folder_id or "").strip()
        enabled = (
            self._explicit_enabled
            if self._explicit_enabled is not None
            else settings.yandex_speechkit_enabled
        )

        if not api_key:
            raise self._error(
                "Yandex SpeechKit API key is not configured",
                "provider_resolve",
                None,
                {"api_key_set": False, "enabled": enabled},
            )

        if voice_id_override:
            voice, emotion = _parse_voice_id(voice_id_override)
        else:
            raw = self._explicit_voice or (settings.yandex_speechkit_voice or "").strip()
            voice, emotion = _parse_voice_id(raw) if raw else ("alena", "neutral")

        # Override emotion from config/env if not embedded in voice_id
        if not voice_id_override:
            env_emotion = self._explicit_emotion or (settings.yandex_speechkit_emotion or "").strip()
            if env_emotion:
                emotion = env_emotion

        return YandexResolvedConfig(
            api_key=api_key,
            folder_id=folder_id,
            voice=voice,
            emotion=emotion,
            config_source=self._config_source,
            enabled=enabled,
        )

    def _build_form(self, text: str, config: YandexResolvedConfig) -> dict:
        data: dict = {
            "text": text,
            "lang": "ru-RU",
            "voice": config.voice,
            "format": _OUTPUT_FORMAT,
            "sampleRateHertz": str(_OUTPUT_SAMPLE_RATE),
        }
        if config.emotion:
            data["emotion"] = config.emotion
        if config.folder_id:
            data["folderId"] = config.folder_id
        return data

    def _make_client(self, config: YandexResolvedConfig) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Api-Key {config.api_key}"},
            timeout=self._timeout,
            transport=self._transport,
        )

    def _error(
        self,
        message: str,
        stage: str,
        config: Optional[YandexResolvedConfig],
        extra: dict,
    ) -> EngineError:
        detail = {
            "provider": "yandex_speechkit",
            "stage": stage,
            "config_source": self._config_source,
            "api_key_set": config is not None,
            "voice": config.voice if config else None,
            **extra,
        }
        log.error("yandex_speechkit.error", **{k: str(v)[:200] for k, v in detail.items()}, message=message)
        return EngineError(message, detail=detail)

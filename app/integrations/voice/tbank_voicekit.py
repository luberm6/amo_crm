"""
T-Bank (Tinkoff) VoiceKit TTS provider — HTTP REST adapter.

T-Bank VoiceKit uses gRPC natively, but also exposes a REST gateway.
Note: Full commercial access requires a T-Bank business account and contract.
A sandbox/trial may be available — check https://voicekit.tinkoff.ru/

This module implements the REST API path. The gRPC path requires
tinkoff-voicekit protobuf bindings which can be added separately.

Required env vars:
  TBANK_VOICEKIT_API_KEY    — API key from T-Bank developer portal
  TBANK_VOICEKIT_SECRET_KEY — Secret key for JWT signing
  TBANK_VOICEKIT_VOICE      — Default voice (e.g. "alyona")
  TBANK_VOICEKIT_ENDPOINT   — API endpoint (default: api.tinkoff.ai)

Voice ID format: "voice_name" or "voice_name:style"
"""
from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

import httpx

from app.core.config import settings
from app.core.exceptions import EngineError
from app.core.logging import get_logger
from app.integrations.voice.base import AbstractVoiceProvider

log = get_logger(__name__)

_DEFAULT_ENDPOINT = "https://api.tinkoff.ai"
_TTS_ENDPOINT = "/v1/tts:synthesize"
_TIMEOUT = 20.0
_OUTPUT_SAMPLE_RATE = 16000
_VALIDATION_TEXT = "Тест голоса"

VOICES: dict[str, dict] = {
    "alyona":     {"gender": "female", "desc": "Алёна — нейтральный"},
    "dorofeev":   {"gender": "male",   "desc": "Дорофеев — нейтральный"},
    "maxim":      {"gender": "male",   "desc": "Максим — нейтральный"},
    "naomi":      {"gender": "female", "desc": "Наоми — нейтральный"},
    "seraphima":  {"gender": "female", "desc": "Серафима — нейтральный"},
}


def _mask(value: str) -> Optional[str]:
    if not value:
        return None
    return value[:4] + "..." + value[-4:] if len(value) > 8 else "***"


def _parse_voice_id(voice_id: str) -> tuple[str, Optional[str]]:
    if ":" in voice_id:
        parts = voice_id.split(":", 1)
        return parts[0].strip(), parts[1].strip() or None
    return voice_id.strip(), None


@dataclass(frozen=True)
class TBankResolvedConfig:
    api_key: str
    secret_key: str
    endpoint: str
    voice: str
    style: Optional[str]
    config_source: str
    enabled: bool

    @property
    def api_key_masked(self) -> Optional[str]:
        return _mask(self.api_key)


class TBankVoiceKitClient(AbstractVoiceProvider):
    """
    T-Bank VoiceKit TTS client (REST gateway).

    voice_id format: "alyona" or "alyona:neutral"
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        default_voice: Optional[str] = None,
        enabled: Optional[bool] = None,
        config_source: str = "env",
        timeout: float = _TIMEOUT,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self._explicit_api_key = (api_key or "").strip()
        self._explicit_secret_key = (secret_key or "").strip()
        self._explicit_endpoint = (endpoint or "").strip()
        self._explicit_voice = (default_voice or "").strip()
        self._explicit_enabled = enabled
        self._config_source = config_source
        self._timeout = timeout
        self._transport = transport

    def runtime_diagnostics(self) -> dict[str, Any]:
        return {
            "provider": "tbank_voicekit",
            "config_source": self._config_source,
            "api_key_set": bool(self._explicit_api_key or settings.tbank_voicekit_api_key),
            "enabled": self._explicit_enabled if self._explicit_enabled is not None else settings.tbank_voicekit_enabled,
        }

    async def validate_tts_contract(self, text: str = _VALIDATION_TEXT) -> bytes:
        return await self.synthesize(text)

    async def synthesize(
        self,
        text: str,
        voice_id: Optional[str] = None,
    ) -> bytes:
        config = self._resolve_config(voice_id)

        log.info(
            "tbank_voicekit.request_started",
            voice=config.voice,
            text_chars=len(text),
        )

        body = {
            "input": {"text": text},
            "audio_config": {
                "audio_encoding": "LINEAR16",
                "sample_rate_hertz": _OUTPUT_SAMPLE_RATE,
                "voice_name": config.voice,
            },
        }

        try:
            async with httpx.AsyncClient(
                base_url=config.endpoint,
                timeout=self._timeout,
                transport=self._transport,
                headers=self._auth_headers(config),
            ) as client:
                resp = await client.post(_TTS_ENDPOINT, json=body)
        except httpx.RequestError as exc:
            raise self._error("T-Bank VoiceKit request failed", "http_request", config, {"error": str(exc)}) from exc

        if resp.status_code >= 400:
            raise self._error(
                f"T-Bank VoiceKit returned HTTP {resp.status_code}",
                "http_request",
                config,
                {"http_status": resp.status_code, "body_preview": resp.text[:400]},
            )

        # Response is JSON with base64-encoded audio
        try:
            import base64
            data = resp.json()
            audio_b64 = data.get("audio_content", "")
            pcm = base64.b64decode(audio_b64)
        except Exception:
            # Might be raw binary depending on API version
            pcm = resp.content

        log.info("tbank_voicekit.request_completed", byte_length=len(pcm), voice=config.voice)
        return pcm

    async def synthesize_streaming(
        self,
        text: str,
        voice_id: Optional[str] = None,
    ) -> AsyncIterator[bytes]:
        pcm = await self.synthesize(text, voice_id)
        yield pcm

    # ── Private ────────────────────────────────────────────────────────────────

    def _resolve_config(self, voice_id_override: Optional[str]) -> TBankResolvedConfig:
        api_key = self._explicit_api_key or (settings.tbank_voicekit_api_key or "").strip()
        secret_key = self._explicit_secret_key or (settings.tbank_voicekit_secret_key or "").strip()
        endpoint = self._explicit_endpoint or (settings.tbank_voicekit_endpoint or "").strip() or _DEFAULT_ENDPOINT
        enabled = (
            self._explicit_enabled
            if self._explicit_enabled is not None
            else settings.tbank_voicekit_enabled
        )

        if not api_key:
            raise self._error(
                "T-Bank VoiceKit API key is not configured",
                "provider_resolve",
                None,
                {"api_key_set": False, "enabled": enabled},
            )

        if voice_id_override:
            voice, style = _parse_voice_id(voice_id_override)
        else:
            raw = self._explicit_voice or (settings.tbank_voicekit_voice or "").strip() or "alyona"
            voice, style = _parse_voice_id(raw)

        return TBankResolvedConfig(
            api_key=api_key,
            secret_key=secret_key,
            endpoint=endpoint.rstrip("/"),
            voice=voice,
            style=style,
            config_source=self._config_source,
            enabled=enabled,
        )

    def _auth_headers(self, config: TBankResolvedConfig) -> dict:
        # T-Bank uses API-Key header; secret_key used for HMAC signing if required
        headers = {"Authorization": f"Bearer {config.api_key}"}
        return headers

    def _error(
        self,
        message: str,
        stage: str,
        config: Optional[TBankResolvedConfig],
        extra: dict,
    ) -> EngineError:
        detail = {
            "provider": "tbank_voicekit",
            "stage": stage,
            "config_source": self._config_source,
            "voice": config.voice if config else None,
            **extra,
        }
        log.error("tbank_voicekit.error", **{k: str(v)[:200] for k, v in detail.items()}, message=message)
        return EngineError(message, detail=detail)

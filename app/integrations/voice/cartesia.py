"""
Cartesia TTS provider for Direct mode / Browser sandbox.

Cartesia delivers ultra-low-latency TTS (~80–150 ms to first audio byte)
with custom voice cloning support. The HTTP streaming endpoint returns
raw PCM-16 at 16 kHz, which matches our internal audio pipeline format
exactly — no resampling required.

API reference: https://docs.cartesia.ai/api-reference/tts/bytes
Voice cloning:  https://docs.cartesia.ai/api-reference/voices/clone
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

_BASE_URL = "https://api.cartesia.ai"
_TTS_ENDPOINT = "/tts/bytes"
_VOICES_ENDPOINT = "/voices"
_TIMEOUT = 20.0
_MODEL_ID = "sonic-2"          # fastest general model; sonic-turbo for even lower cost
_OUTPUT_SAMPLE_RATE = 16000
_OUTPUT_FORMAT = "raw"         # raw PCM-16, no container
_OUTPUT_ENCODING = "pcm_s16le" # signed 16-bit little-endian
_CARTESIA_VERSION = "2025-04-16"
_VALIDATION_TEXT = "Тест"
_MAX_ERROR_BODY_PREVIEW = 400


def _mask(value: str) -> Optional[str]:
    if not value:
        return None
    return value[:4] + "..." + value[-4:] if len(value) > 8 else "***"


@dataclass(frozen=True)
class CartesiaResolvedConfig:
    api_key: str
    voice_id: str
    api_key_set: bool
    voice_id_source: str
    config_source: str
    enabled: bool
    model_id: str

    @property
    def voice_id_masked(self) -> Optional[str]:
        return _mask(self.voice_id)


class CartesiaClient(AbstractVoiceProvider):
    """
    Cartesia runtime client.

    Credentials are resolved from env-backed settings on every call so that
    the same client object can be reused across multiple sessions without
    becoming stale.
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        default_voice_id: Optional[str] = None,
        enabled: Optional[bool] = None,
        config_source: str = "env",
        model_id: str = _MODEL_ID,
        base_url: str = _BASE_URL,
        timeout: float = _TIMEOUT,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self._explicit_api_key = (api_key or "").strip()
        self._explicit_voice_id = (default_voice_id or "").strip()
        self._explicit_enabled = enabled
        self._config_source = config_source
        self._model_id = model_id
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport

    # ── Public API ─────────────────────────────────────────────────────────────

    def runtime_diagnostics(self) -> dict[str, Any]:
        try:
            config = self._resolve_config(None)
        except EngineError as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            return {
                "provider": "cartesia",
                "config_source": self._config_source,
                "api_key_set": bool(self._explicit_api_key or settings.cartesia_api_key),
                "voice_id_source": detail.get("voice_id_source", "unavailable"),
                "enabled": self._explicit_enabled if self._explicit_enabled is not None else settings.cartesia_enabled,
                "stage": detail.get("stage", "provider_resolve"),
            }
        return {
            "provider": "cartesia",
            "config_source": config.config_source,
            "api_key_set": config.api_key_set,
            "voice_id_source": config.voice_id_source,
            "voice_id_masked": config.voice_id_masked,
            "enabled": config.enabled,
            "model_id": config.model_id,
        }

    async def validate_tts_contract(self, text: str = _VALIDATION_TEXT) -> bytes:
        return await self.synthesize(text)

    async def synthesize(
        self,
        text: str,
        voice_id: Optional[str] = None,
    ) -> bytes:
        config = self._resolve_config(voice_id)
        body = self._build_body(text, config)

        log.info(
            "cartesia.request_started",
            endpoint=_TTS_ENDPOINT,
            config_source=config.config_source,
            voice_id_masked=config.voice_id_masked,
            model_id=config.model_id,
            text_chars=len(text),
            streaming=False,
        )

        try:
            async with self._make_client(config) as client:
                resp = await client.post(_TTS_ENDPOINT, json=body)
        except httpx.RequestError as exc:
            raise self._error("Cartesia request failed", "http_request", config, {"error": str(exc)}) from exc

        if resp.status_code >= 400:
            raise self._error(
                f"Cartesia returned HTTP {resp.status_code}",
                "http_request",
                config,
                {"http_status": resp.status_code, "body_preview": resp.text[:_MAX_ERROR_BODY_PREVIEW]},
            )

        pcm = resp.content
        log.info(
            "cartesia.request_completed",
            byte_length=len(pcm),
            voice_id_masked=config.voice_id_masked,
            model_id=config.model_id,
        )
        return pcm

    async def synthesize_streaming(
        self,
        text: str,
        voice_id: Optional[str] = None,
    ) -> AsyncIterator[bytes]:
        config = self._resolve_config(voice_id)
        body = self._build_body(text, config)

        log.info(
            "cartesia.request_started",
            endpoint=_TTS_ENDPOINT,
            config_source=config.config_source,
            voice_id_masked=config.voice_id_masked,
            model_id=config.model_id,
            text_chars=len(text),
            streaming=True,
        )

        try:
            async with self._make_client(config) as client:
                async with client.stream("POST", _TTS_ENDPOINT, json=body) as resp:
                    if resp.status_code >= 400:
                        body_preview = await resp.aread()
                        raise self._error(
                            f"Cartesia streaming returned HTTP {resp.status_code}",
                            "http_stream",
                            config,
                            {"http_status": resp.status_code, "body_preview": body_preview[:_MAX_ERROR_BODY_PREVIEW].decode(errors="replace")},
                        )
                    chunk_count = 0
                    async for chunk in resp.aiter_bytes(chunk_size=3200):  # 100ms at 16kHz PCM16
                        if chunk:
                            chunk_count += 1
                            yield chunk
                    log.info(
                        "cartesia.stream_completed",
                        chunk_count=chunk_count,
                        voice_id_masked=config.voice_id_masked,
                    )
        except httpx.RequestError as exc:
            raise self._error("Cartesia streaming request failed", "http_stream", config, {"error": str(exc)}) from exc

    async def list_voices(self) -> list[dict]:
        """Fetch all voices from Cartesia — used for voice ID discovery in admin."""
        config = self._resolve_config(None)
        try:
            async with self._make_client(config) as client:
                resp = await client.get(_VOICES_ENDPOINT)
            if resp.status_code >= 400:
                return []
            return resp.json()
        except Exception:
            return []

    # ── Private ────────────────────────────────────────────────────────────────

    def _resolve_config(self, voice_id_override: Optional[str]) -> CartesiaResolvedConfig:
        api_key = (
            self._explicit_api_key
            or (settings.cartesia_api_key or "").strip()
        )
        enabled = (
            self._explicit_enabled
            if self._explicit_enabled is not None
            else settings.cartesia_enabled
        )

        if not api_key:
            raise self._error(
                "Cartesia API key is not configured",
                "provider_resolve",
                None,
                {"api_key_set": False, "voice_id_source": "none", "voice_id_masked": None, "enabled": enabled},
            )

        if voice_id_override:
            voice_id = voice_id_override.strip()
            voice_id_source = "call_override"
        elif self._explicit_voice_id:
            voice_id = self._explicit_voice_id
            voice_id_source = "client_init"
        elif settings.cartesia_voice_id:
            voice_id = settings.cartesia_voice_id.strip()
            voice_id_source = "env_settings"
        else:
            raise self._error(
                "Cartesia voice ID is not configured",
                "provider_resolve",
                None,
                {"api_key_set": bool(api_key), "voice_id_source": "none", "voice_id_masked": None, "enabled": enabled},
            )

        return CartesiaResolvedConfig(
            api_key=api_key,
            voice_id=voice_id,
            api_key_set=True,
            voice_id_source=voice_id_source,
            config_source=self._config_source,
            enabled=enabled,
            model_id=self._model_id,
        )

    def _build_body(self, text: str, config: CartesiaResolvedConfig) -> dict:
        return {
            "model_id": config.model_id,
            "transcript": text,
            "voice": {"mode": "id", "id": config.voice_id},
            "output_format": {
                "container": _OUTPUT_FORMAT,
                "encoding": _OUTPUT_ENCODING,
                "sample_rate": _OUTPUT_SAMPLE_RATE,
            },
            "language": "ru",
        }

    def _make_client(self, config: CartesiaResolvedConfig) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "X-API-Key": config.api_key,
                "Cartesia-Version": _CARTESIA_VERSION,
                "Content-Type": "application/json",
            },
            timeout=self._timeout,
            transport=self._transport,
        )

    def _error(
        self,
        message: str,
        stage: str,
        config: Optional[CartesiaResolvedConfig],
        extra: dict,
    ) -> EngineError:
        detail = {
            "provider": "cartesia",
            "stage": stage,
            "config_source": self._config_source,
            "api_key_set": config.api_key_set if config else bool(self._explicit_api_key or settings.cartesia_api_key),
            "voice_id_masked": config.voice_id_masked if config else None,
            "voice_id_source": config.voice_id_source if config else "none",
            "enabled": config.enabled if config else settings.cartesia_enabled,
            **extra,
        }
        log.error("cartesia.error", **{k: str(v)[:200] for k, v in detail.items()}, message=message)
        return EngineError(message, detail=detail)

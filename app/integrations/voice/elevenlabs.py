"""
ElevenLabs TTS provider for Direct mode / Browser sandbox.

The integration contract is intentionally explicit:
- runtime requests PCM16 @ 16kHz
- request/response stages are logged with enough context to debug failures
- configuration resolution is deterministic and never logs raw secrets

Important current runtime rule:
- live runtime still resolves ElevenLabs credentials from env-backed settings
- provider settings UI validates and stores credentials independently
- provider settings do not silently rewire the active runtime path
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

import httpx

from app.core.audio_utils import (
    Pcm16ChunkAligner,
    dump_pcm16le_wav,
    pcm16le_stats,
)
from app.core.config import settings
from app.core.exceptions import EngineError
from app.core.logging import get_logger
from app.integrations.voice.base import AbstractVoiceProvider

log = get_logger(__name__)

_BASE_URL = "https://api.elevenlabs.io"
_TIMEOUT = 30.0
# Flash model is the practical default for realtime browser/direct voice:
# live measurements showed materially lower first-byte latency than
# eleven_multilingual_v2 while preserving clean PCM output for this path.
_MODEL_ID = "eleven_flash_v2_5"
_OUTPUT_FORMAT = "pcm_16000"
_VALIDATION_TEXT = "Тест"
_MAX_ERROR_BODY_PREVIEW = 400
_ALLOWED_AUDIO_CONTENT_TYPES = (
    "audio/",
    "application/octet-stream",
    "binary/octet-stream",
)


@dataclass(frozen=True)
class ElevenLabsResolvedConfig:
    api_key: str
    voice_id: str
    api_key_set: bool
    voice_id_source: str
    config_source: str
    enabled: bool
    model_id: str
    output_format: str

    @property
    def voice_id_masked(self) -> Optional[str]:
        return _mask_value(self.voice_id)


class ElevenLabsClient(AbstractVoiceProvider):
    """
    ElevenLabs runtime client.

    By default it resolves credentials from env-backed settings on every request,
    so the request contract stays accurate even if the process reuses the same
    provider object across multiple sessions.
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        default_voice_id: Optional[str] = None,
        enabled: Optional[bool] = None,
        config_source: str = "env",
        model_id: str = _MODEL_ID,
        output_format: str = _OUTPUT_FORMAT,
        base_url: str = _BASE_URL,
        timeout: float = _TIMEOUT,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self._explicit_api_key = (api_key or "").strip()
        self._explicit_voice_id = (default_voice_id or "").strip()
        self._explicit_enabled = enabled
        self._config_source = config_source
        self._model_id = model_id
        self._output_format = output_format
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport

    def runtime_diagnostics(self) -> dict[str, Any]:
        try:
            config = self._resolve_runtime_config(None)
        except EngineError as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            return {
                "provider": "elevenlabs",
                "config_source": self._config_source,
                "api_key_set": detail.get("api_key_set", bool(self._explicit_api_key or settings.elevenlabs_api_key)),
                "voice_id_source": detail.get("voice_id_source", "unavailable"),
                "voice_id_masked": detail.get("voice_id_masked"),
                "enabled": detail.get("enabled", self._explicit_enabled if self._explicit_enabled is not None else settings.elevenlabs_enabled),
                "stage": detail.get("stage", "provider_resolve"),
            }
        return {
            "provider": "elevenlabs",
            "config_source": config.config_source,
            "api_key_set": config.api_key_set,
            "voice_id_source": config.voice_id_source,
            "voice_id_masked": config.voice_id_masked,
            "enabled": config.enabled,
            "model_id": config.model_id,
            "output_format": config.output_format,
        }

    async def validate_tts_contract(self, text: str = _VALIDATION_TEXT) -> bytes:
        return await self.synthesize(text)

    async def synthesize(
        self,
        text: str,
        voice_id: Optional[str] = None,
    ) -> bytes:
        config = self._resolve_runtime_config(voice_id)
        request = self._build_request(text=text, config=config, streaming=False)

        log.info(
            "elevenlabs.request_started",
            provider="elevenlabs",
            stage="request_build",
            endpoint=request["path"],
            config_source=config.config_source,
            voice_id_source=config.voice_id_source,
            voice_id_masked=config.voice_id_masked,
            api_key_set=config.api_key_set,
            model_id=config.model_id,
            output_format=config.output_format,
            streaming=False,
            text_chars=len(text),
        )

        try:
            async with self._make_client(config) as client:
                response = await client.post(
                    request["path"],
                    params=request["params"],
                    json=request["json"],
                )
        except httpx.RequestError as exc:
            raise self._engine_error(
                message="ElevenLabs request failed",
                stage="http_request",
                config=config,
                extra={"error": str(exc)},
            ) from exc

        if response.status_code >= 400:
            raise self._engine_error(
                message=f"ElevenLabs returned HTTP {response.status_code}",
                stage="http_request",
                config=config,
                extra={
                    "http_status": response.status_code,
                    "content_type": response.headers.get("content-type"),
                    "body_preview": _truncate_text(response.text),
                },
            )

        content = response.content
        content_type = response.headers.get("content-type", "")
        self._ensure_audio_payload(
            content_type=content_type,
            byte_length=len(content),
            config=config,
            stage="response_parse",
        )

        log.info(
            "elevenlabs.response_received",
            provider="elevenlabs",
            stage="response_parse",
            config_source=config.config_source,
            voice_id_source=config.voice_id_source,
            voice_id_masked=config.voice_id_masked,
            api_key_set=config.api_key_set,
            content_type=content_type,
            byte_length=len(content),
            streaming=False,
        )
        stats = pcm16le_stats(content)
        log.info(
            "elevenlabs.response_audio_metadata",
            provider="elevenlabs",
            stage="response_parse",
            config_source=config.config_source,
            voice_id_source=config.voice_id_source,
            voice_id_masked=config.voice_id_masked,
            api_key_set=config.api_key_set,
            **stats,
        )
        artifact_path = dump_pcm16le_wav(
            "raw_elevenlabs_response",
            content,
        )
        if artifact_path:
            log.info(
                "elevenlabs.audio_bytes_received",
                provider="elevenlabs",
                stage="response_parse",
                config_source=config.config_source,
                voice_id_source=config.voice_id_source,
                byte_length=len(content),
                artifact_path=artifact_path,
            )
        return content

    async def synthesize_streaming(
        self,
        text: str,
        voice_id: Optional[str] = None,
    ) -> AsyncIterator[bytes]:
        config = self._resolve_runtime_config(voice_id)
        request = self._build_request(text=text, config=config, streaming=True)

        log.info(
            "elevenlabs.request_started",
            provider="elevenlabs",
            stage="request_build",
            endpoint=request["path"],
            config_source=config.config_source,
            voice_id_source=config.voice_id_source,
            voice_id_masked=config.voice_id_masked,
            api_key_set=config.api_key_set,
            model_id=config.model_id,
            output_format=config.output_format,
            streaming=True,
            text_chars=len(text),
        )

        try:
            async with self._make_client(config) as client:
                async with client.stream(
                    "POST",
                    request["path"],
                    params=request["params"],
                    json=request["json"],
                ) as response:
                    if response.status_code >= 400:
                        body_preview = await _read_error_preview(response)
                        raise self._engine_error(
                            message=f"ElevenLabs returned HTTP {response.status_code}",
                            stage="http_request",
                            config=config,
                            extra={
                                "http_status": response.status_code,
                                "content_type": response.headers.get("content-type"),
                                "body_preview": body_preview,
                            },
                        )

                    content_type = response.headers.get("content-type", "")
                    total_bytes = 0
                    self._ensure_audio_payload(
                        content_type=content_type,
                        byte_length=None,
                        config=config,
                        stage="response_parse",
                    )
                    log.info(
                        "elevenlabs.response_received",
                        provider="elevenlabs",
                        stage="response_parse",
                        config_source=config.config_source,
                        voice_id_source=config.voice_id_source,
                        voice_id_masked=config.voice_id_masked,
                        api_key_set=config.api_key_set,
                        content_type=content_type,
                        streaming=True,
                    )

                    aligner = Pcm16ChunkAligner()
                    chunk_index = 0
                    emitted_bytes = 0
                    odd_chunk_log_samples = 0
                    raw_stream = bytearray()

                    async for chunk in response.aiter_bytes():
                        if not chunk:
                            continue
                        chunk_index += 1
                        total_bytes += len(chunk)
                        raw_stream.extend(chunk)
                        aligned = aligner.push(chunk)
                        should_log_chunk = chunk_index <= 3 or chunk_index % 50 == 0
                        if len(chunk) % 2 != 0 and odd_chunk_log_samples < 5:
                            should_log_chunk = True
                            odd_chunk_log_samples += 1
                        if should_log_chunk:
                            log.info(
                                "elevenlabs.audio_bytes_received",
                                provider="elevenlabs",
                                stage="response_stream",
                                config_source=config.config_source,
                                voice_id_source=config.voice_id_source,
                                voice_id_masked=config.voice_id_masked,
                                chunk_index=chunk_index,
                                byte_length=len(chunk),
                                odd_length=bool(len(chunk) % 2),
                                odd_chunk_log_samples=odd_chunk_log_samples,
                                first_bytes_hex=chunk[:12].hex(),
                            )
                        if aligned:
                            emitted_bytes += len(aligned)
                            yield aligned

                    final_chunk = aligner.flush(pad_final_byte=True)
                    if final_chunk:
                        emitted_bytes += len(final_chunk)
                        log.warning(
                            "elevenlabs.response_stream_padded_final_byte",
                            provider="elevenlabs",
                            config_source=config.config_source,
                            voice_id_source=config.voice_id_source,
                            voice_id_masked=config.voice_id_masked,
                            padded_bytes=len(final_chunk),
                        )
                        yield final_chunk

                    if total_bytes == 0:
                        raise self._engine_error(
                            message="ElevenLabs returned an empty audio stream",
                            stage="response_parse",
                            config=config,
                            extra={
                                "http_status": response.status_code,
                                "content_type": content_type,
                                "byte_length": total_bytes,
                            },
                        )
                    stream_stats = pcm16le_stats(bytes(raw_stream[:len(raw_stream) - (len(raw_stream) % 2)]))
                    log.info(
                        "elevenlabs.response_audio_metadata",
                        provider="elevenlabs",
                        stage="response_stream",
                        config_source=config.config_source,
                        voice_id_source=config.voice_id_source,
                        voice_id_masked=config.voice_id_masked,
                        api_key_set=config.api_key_set,
                        chunk_count=chunk_index,
                        raw_byte_length=total_bytes,
                        aligned_byte_length=emitted_bytes,
                        odd_chunks=aligner.odd_chunks,
                        **stream_stats,
                    )
                    artifact_path = dump_pcm16le_wav(
                        "raw_elevenlabs_stream",
                        bytes(raw_stream),
                    )
                    if artifact_path:
                        log.info(
                            "elevenlabs.audio_bytes_received",
                            provider="elevenlabs",
                            stage="response_stream",
                            config_source=config.config_source,
                            voice_id_source=config.voice_id_source,
                            raw_byte_length=total_bytes,
                            aligned_byte_length=emitted_bytes,
                            odd_chunks=aligner.odd_chunks,
                            artifact_path=artifact_path,
                        )
        except EngineError:
            raise
        except httpx.RequestError as exc:
            raise self._engine_error(
                message="ElevenLabs request failed",
                stage="http_request",
                config=config,
                extra={"error": str(exc)},
            ) from exc

    async def close(self) -> None:
        # Requests use short-lived AsyncClient instances; nothing persistent to close.
        return None

    def _resolve_runtime_config(self, voice_id_override: Optional[str]) -> ElevenLabsResolvedConfig:
        enabled = self._explicit_enabled if self._explicit_enabled is not None else settings.elevenlabs_enabled
        api_key = (self._explicit_api_key or settings.elevenlabs_api_key or "").strip()
        if voice_id_override:
            voice_id = voice_id_override.strip()
            voice_id_source = "override"
        elif self._explicit_voice_id:
            voice_id = self._explicit_voice_id
            voice_id_source = "constructor"
        else:
            voice_id = (settings.elevenlabs_voice_id or "").strip()
            voice_id_source = "env"

        detail = {
            "provider": "elevenlabs",
            "stage": "provider_resolve",
            "config_source": self._config_source,
            "api_key_set": bool(api_key),
            "voice_id_source": voice_id_source,
            "voice_id_masked": _mask_value(voice_id),
            "enabled": bool(enabled),
        }
        if not enabled:
            raise EngineError("ElevenLabs provider is disabled.", detail=detail)
        if not api_key:
            raise EngineError("ElevenLabs API key is not configured.", detail=detail)
        if not voice_id:
            raise EngineError("ElevenLabs voice ID is not configured.", detail=detail)

        return ElevenLabsResolvedConfig(
            api_key=api_key,
            voice_id=voice_id,
            api_key_set=True,
            voice_id_source=voice_id_source,
            config_source=self._config_source,
            enabled=True,
            model_id=self._model_id,
            output_format=self._output_format,
        )

    def _build_request(
        self,
        *,
        text: str,
        config: ElevenLabsResolvedConfig,
        streaming: bool,
    ) -> dict[str, Any]:
        path = f"/v1/text-to-speech/{config.voice_id}"
        if streaming:
            path += "/stream"
        return {
            "path": path,
            "params": {"output_format": config.output_format},
            "json": {
                "text": text,
                "model_id": config.model_id,
            },
        }

    def _make_client(self, config: ElevenLabsResolvedConfig) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            headers={
                "xi-api-key": config.api_key,
                "Content-Type": "application/json",
                "Accept": "audio/pcm",
            },
            transport=self._transport,
        )

    def _ensure_audio_payload(
        self,
        *,
        content_type: str,
        byte_length: Optional[int],
        config: ElevenLabsResolvedConfig,
        stage: str,
    ) -> None:
        lowered = (content_type or "").lower()
        if lowered and any(token in lowered for token in _ALLOWED_AUDIO_CONTENT_TYPES):
            if byte_length is not None and byte_length <= 0:
                raise self._engine_error(
                    message="ElevenLabs returned an empty audio payload",
                    stage=stage,
                    config=config,
                    extra={"content_type": content_type, "byte_length": byte_length},
                )
            return
        raise self._engine_error(
            message="ElevenLabs response is not an audio payload",
            stage=stage,
            config=config,
            extra={"content_type": content_type, "byte_length": byte_length},
        )

    def _engine_error(
        self,
        *,
        message: str,
        stage: str,
        config: ElevenLabsResolvedConfig,
        extra: Optional[dict[str, Any]] = None,
    ) -> EngineError:
        detail = {
            "provider": "elevenlabs",
            "stage": stage,
            "config_source": config.config_source,
            "api_key_set": config.api_key_set,
            "voice_id_source": config.voice_id_source,
            "voice_id_masked": config.voice_id_masked,
            "model_id": config.model_id,
            "output_format": config.output_format,
        }
        if extra:
            detail.update(extra)
        return EngineError(message, detail=detail)


async def _read_error_preview(response: httpx.Response) -> str:
    try:
        raw = await response.aread()
    except Exception:
        return "<unavailable>"
    return _truncate_text(raw.decode("utf-8", errors="replace"))


def _truncate_text(value: str) -> str:
    if len(value) <= _MAX_ERROR_BODY_PREVIEW:
        return value
    return value[:_MAX_ERROR_BODY_PREVIEW] + "..."


def _mask_value(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}***{value[-2:]}"

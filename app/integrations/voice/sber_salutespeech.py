"""
Sber SaluteSpeech TTS provider (REST API).

SaluteSpeech delivers natural Russian speech synthesis with SSML support.
Auth: OAuth2 client credentials (token expires every 30 min, auto-refreshed).
Latency: ~400–800 ms.

API reference: https://developers.sber.ru/docs/ru/salutespeech/rest/synthesis
OAuth docs:    https://developers.sber.ru/docs/ru/salutespeech/authentication

Required env vars:
  SBER_SALUTESPEECH_CLIENT_ID     — From Sber developer portal
  SBER_SALUTESPEECH_CLIENT_SECRET — From Sber developer portal
  SBER_SALUTESPEECH_SCOPE         — SALUTE_SPEECH_PERS (B2C) or SALUTE_SPEECH_CORP (B2B)
  SBER_SALUTESPEECH_VOICE         — Default voice (e.g. "Nec_24000")

Voice ID format: "voice_name" or "voice_name:style"
  Examples: "Nec_24000", "May_24000", "Bys_24000", "Tur_24000"
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Optional

import httpx

from app.core.config import settings
from app.core.exceptions import EngineError
from app.core.logging import get_logger
from app.integrations.voice.base import AbstractVoiceProvider

log = get_logger(__name__)

_AUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
_TTS_URL = "https://smartspeech.sber.ru/rest/v1/text:synthesize"
_TIMEOUT = 25.0
_OUTPUT_FORMAT = "pcm16"
_OUTPUT_SAMPLE_RATE = 16000
_TOKEN_LIFETIME_SECONDS = 1700   # tokens expire in 30 min, refresh at 28 min
_VALIDATION_TEXT = "Тест голоса"

# Available SaluteSpeech voices
VOICES: dict[str, dict] = {
    "Nec_24000": {"gender": "female", "desc": "Наталья — нейтральный, деловой"},
    "May_24000": {"gender": "female", "desc": "Майя — дружелюбный, тёплый"},
    "Bys_24000": {"gender": "male",   "desc": "Борис — нейтральный, уверенный"},
    "Tur_24000": {"gender": "male",   "desc": "Тур — нейтральный, спокойный"},
    "Ost_24000": {"gender": "female", "desc": "Оксана — нейтральный"},
    "Pon_24000": {"gender": "male",   "desc": "Пётр — нейтральный"},
}


@dataclass
class _TokenCache:
    token: str = ""
    expires_at: datetime = field(default_factory=lambda: datetime.min.replace(tzinfo=timezone.utc))

    def is_valid(self) -> bool:
        return bool(self.token) and datetime.now(timezone.utc) < self.expires_at


def _mask(value: str) -> Optional[str]:
    if not value:
        return None
    return value[:4] + "..." + value[-4:] if len(value) > 8 else "***"


def _parse_voice_id(voice_id: str) -> tuple[str, Optional[str]]:
    """Parse 'voice_name:style' or 'voice_name' → (voice, style)."""
    if ":" in voice_id:
        parts = voice_id.split(":", 1)
        return parts[0].strip(), parts[1].strip() or None
    return voice_id.strip(), None


@dataclass(frozen=True)
class SberResolvedConfig:
    client_id: str
    client_secret: str
    scope: str
    voice: str
    style: Optional[str]
    config_source: str
    enabled: bool


class SberSaluteSpeechClient(AbstractVoiceProvider):
    """
    Sber SaluteSpeech TTS client with automatic token refresh.

    voice_id format: "Nec_24000" or "Nec_24000:formal"
    """

    def __init__(
        self,
        *,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        scope: Optional[str] = None,
        default_voice: Optional[str] = None,
        enabled: Optional[bool] = None,
        config_source: str = "env",
        timeout: float = _TIMEOUT,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self._explicit_client_id = (client_id or "").strip()
        self._explicit_client_secret = (client_secret or "").strip()
        self._explicit_scope = (scope or "").strip()
        self._explicit_voice = (default_voice or "").strip()
        self._explicit_enabled = enabled
        self._config_source = config_source
        self._timeout = timeout
        self._transport = transport
        self._token_cache = _TokenCache()
        self._token_lock = asyncio.Lock()

    def runtime_diagnostics(self) -> dict[str, Any]:
        return {
            "provider": "sber_salutespeech",
            "config_source": self._config_source,
            "client_id_set": bool(self._explicit_client_id or settings.sber_salutespeech_client_id),
            "enabled": self._explicit_enabled if self._explicit_enabled is not None else settings.sber_salutespeech_enabled,
            "token_cached": self._token_cache.is_valid(),
        }

    async def validate_tts_contract(self, text: str = _VALIDATION_TEXT) -> bytes:
        return await self.synthesize(text)

    async def synthesize(
        self,
        text: str,
        voice_id: Optional[str] = None,
    ) -> bytes:
        config = self._resolve_config(voice_id)
        token = await self._get_token(config)
        body = self._build_body(text, config)

        log.info(
            "sber_salutespeech.request_started",
            voice=config.voice,
            text_chars=len(text),
        )

        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                resp = await client.post(
                    _TTS_URL,
                    content=body.encode("utf-8"),
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/ssml",
                        "RqUID": str(uuid.uuid4()),
                    },
                    params={"format": _OUTPUT_FORMAT, "voice": config.voice},
                )
        except httpx.RequestError as exc:
            raise self._error("Sber SaluteSpeech request failed", "http_request", config, {"error": str(exc)}) from exc

        if resp.status_code >= 400:
            raise self._error(
                f"Sber SaluteSpeech returned HTTP {resp.status_code}",
                "http_request",
                config,
                {"http_status": resp.status_code, "body_preview": resp.text[:400]},
            )

        pcm = resp.content
        log.info("sber_salutespeech.request_completed", byte_length=len(pcm), voice=config.voice)
        return pcm

    async def synthesize_streaming(
        self,
        text: str,
        voice_id: Optional[str] = None,
    ) -> AsyncIterator[bytes]:
        # SaluteSpeech REST does not support chunked streaming — return full response
        pcm = await self.synthesize(text, voice_id)
        yield pcm

    # ── Private ────────────────────────────────────────────────────────────────

    def _resolve_config(self, voice_id_override: Optional[str]) -> SberResolvedConfig:
        client_id = self._explicit_client_id or (settings.sber_salutespeech_client_id or "").strip()
        client_secret = self._explicit_client_secret or (settings.sber_salutespeech_client_secret or "").strip()
        scope = self._explicit_scope or (settings.sber_salutespeech_scope or "").strip() or "SALUTE_SPEECH_PERS"
        enabled = (
            self._explicit_enabled
            if self._explicit_enabled is not None
            else settings.sber_salutespeech_enabled
        )

        if not client_id or not client_secret:
            raise self._error(
                "Sber SaluteSpeech credentials are not configured",
                "provider_resolve",
                None,
                {"client_id_set": bool(client_id), "enabled": enabled},
            )

        if voice_id_override:
            voice, style = _parse_voice_id(voice_id_override)
        else:
            raw = self._explicit_voice or (settings.sber_salutespeech_voice or "").strip() or "Nec_24000"
            voice, style = _parse_voice_id(raw)

        return SberResolvedConfig(
            client_id=client_id,
            client_secret=client_secret,
            scope=scope,
            voice=voice,
            style=style,
            config_source=self._config_source,
            enabled=enabled,
        )

    async def _get_token(self, config: SberResolvedConfig) -> str:
        async with self._token_lock:
            if self._token_cache.is_valid():
                return self._token_cache.token
            token = await self._fetch_token(config)
            self._token_cache.token = token
            self._token_cache.expires_at = datetime.now(timezone.utc) + timedelta(seconds=_TOKEN_LIFETIME_SECONDS)
            return token

    async def _fetch_token(self, config: SberResolvedConfig) -> str:
        import base64
        creds = base64.b64encode(f"{config.client_id}:{config.client_secret}".encode()).decode()
        try:
            async with httpx.AsyncClient(timeout=10.0, transport=self._transport, verify=False) as client:
                resp = await client.post(
                    _AUTH_URL,
                    data={"scope": config.scope},
                    headers={
                        "Authorization": f"Basic {creds}",
                        "RqUID": str(uuid.uuid4()),
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                )
        except httpx.RequestError as exc:
            raise self._error("Sber token request failed", "auth", config, {"error": str(exc)}) from exc

        if resp.status_code >= 400:
            raise self._error(
                f"Sber OAuth returned HTTP {resp.status_code}",
                "auth",
                config,
                {"http_status": resp.status_code, "body_preview": resp.text[:400]},
            )

        token = resp.json().get("access_token", "")
        if not token:
            raise self._error("Sber OAuth returned empty token", "auth", config, {})
        log.info("sber_salutespeech.token_refreshed")
        return token

    def _build_body(self, text: str, config: SberResolvedConfig) -> str:
        # Wrap in SSML for consistent handling and future markup support
        return f'<speak>{text}</speak>'

    def _error(
        self,
        message: str,
        stage: str,
        config: Optional[SberResolvedConfig],
        extra: dict,
    ) -> EngineError:
        detail = {
            "provider": "sber_salutespeech",
            "stage": stage,
            "config_source": self._config_source,
            "voice": config.voice if config else None,
            **extra,
        }
        log.error("sber_salutespeech.error", **{k: str(v)[:200] for k, v in detail.items()}, message=message)
        return EngineError(message, detail=detail)

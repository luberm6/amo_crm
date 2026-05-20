"""
TTS API endpoints for Voice Lab.

Endpoints:
  GET  /v1/tts/providers                    — list all configured providers with voices
  POST /v1/tts/preview                      — generate audio preview (base64 WAV)
  POST /v1/agents/{agent_id}/voice-config   — save voice config to agent

Preview results are cached in-memory (max 100 entries, 5 min TTL).
"""
from __future__ import annotations

import base64
import hashlib
import struct
import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_auth import require_admin_auth
from app.api.deps import get_db
from app.core.config import settings
from app.core.exceptions import EngineError
from app.core.logging import get_logger
from app.integrations.voice.yandex_speechkit import VOICES as YANDEX_VOICES, EMOTIONS as YANDEX_EMOTIONS
from app.integrations.voice.sber_salutespeech import VOICES as SBER_VOICES
from app.integrations.voice.tbank_voicekit import VOICES as TBANK_VOICES

router = APIRouter(prefix="/tts", tags=["tts"], dependencies=[Depends(require_admin_auth)])
log = get_logger(__name__)

_PREVIEW_MAX_TEXT_LEN = 300
_CACHE_MAX_SIZE = 100
_CACHE_TTL_SECONDS = 300

# Simple in-memory preview cache: key → (pcm_bytes, wav_bytes, timestamp)
_preview_cache: dict[str, tuple[bytes, float]] = {}


def _cache_key(provider: str, voice_id: str, text: str) -> str:
    raw = f"{provider}:{voice_id}:{text}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _cache_get(key: str) -> Optional[bytes]:
    entry = _preview_cache.get(key)
    if entry and (time.time() - entry[1]) < _CACHE_TTL_SECONDS:
        return entry[0]
    if entry:
        _preview_cache.pop(key, None)
    return None


def _cache_put(key: str, wav: bytes) -> None:
    if len(_preview_cache) >= _CACHE_MAX_SIZE:
        # Evict oldest entry
        oldest = min(_preview_cache, key=lambda k: _preview_cache[k][1])
        _preview_cache.pop(oldest, None)
    _preview_cache[key] = (wav, time.time())


def _pcm_to_wav(pcm: bytes, sample_rate: int = 16000, channels: int = 1, bits: int = 16) -> bytes:
    """Wrap raw PCM16LE bytes in a WAV container for browser playback."""
    data_size = len(pcm)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,               # chunk size
        1,                # PCM format
        channels,
        sample_rate,
        sample_rate * channels * (bits // 8),  # byte rate
        channels * (bits // 8),                # block align
        bits,
        b"data",
        data_size,
    )
    return header + pcm


# ── Schemas ────────────────────────────────────────────────────────────────────

class VoiceOption(BaseModel):
    id: str
    name: str
    gender: Optional[str] = None
    description: Optional[str] = None
    emotions: list[str] = []


class ProviderInfo(BaseModel):
    provider: str
    display_name: str
    enabled: bool
    configured: bool
    voices: list[VoiceOption]
    note: Optional[str] = None


class PreviewRequest(BaseModel):
    provider: str
    voice_id: str
    text: str = Field(default="Здравствуйте! Я голосовой помощник кафе Любава.", max_length=_PREVIEW_MAX_TEXT_LEN)
    emotion: Optional[str] = None


class PreviewResponse(BaseModel):
    provider: str
    voice_id: str
    wav_base64: str          # WAV file encoded as base64 for <audio> tag
    duration_ms: Optional[float]
    latency_ms: float
    cached: bool
    byte_size: int


class VoiceConfigSaveRequest(BaseModel):
    provider: str           # e.g. "yandex_speechkit"
    voice_id: str           # e.g. "alena:good"
    voice_strategy: str     # "tts_primary" or "gemini_primary"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _build_provider_list() -> list[ProviderInfo]:
    providers = []

    # Cartesia — voices are UUIDs set per-user, show the configured one
    cartesia_voice_id = settings.cartesia_voice_id
    cartesia_voices = [
        VoiceOption(id=cartesia_voice_id, name="Клонированный голос", description="Настроен в провайдерах")
    ] if cartesia_voice_id else []
    providers.append(ProviderInfo(
        provider="cartesia",
        display_name="Cartesia",
        enabled=settings.cartesia_enabled,
        configured=settings.cartesia_configured,
        voices=cartesia_voices,
        note="Самый быстрый — ~80–150 мс. Поддерживает клонирование голоса.",
    ))

    # Yandex SpeechKit
    yandex_voices = [
        VoiceOption(
            id=vid,
            name=info["desc"],
            gender=info["gender"],
            emotions=YANDEX_EMOTIONS.get(vid, []),
        )
        for vid, info in YANDEX_VOICES.items()
    ]
    providers.append(ProviderInfo(
        provider="yandex_speechkit",
        display_name="Yandex SpeechKit",
        enabled=settings.yandex_speechkit_enabled,
        configured=settings.yandex_speechkit_configured,
        voices=yandex_voices,
        note="Лучшее качество для русского языка. Поддерживает эмоции.",
    ))

    # Sber SaluteSpeech
    sber_voices = [
        VoiceOption(id=vid, name=info["desc"], gender=info["gender"])
        for vid, info in SBER_VOICES.items()
    ]
    providers.append(ProviderInfo(
        provider="sber_salutespeech",
        display_name="Sber SaluteSpeech",
        enabled=settings.sber_salutespeech_enabled,
        configured=settings.sber_salutespeech_configured,
        voices=sber_voices,
        note="Нейронные голоса, SSML поддержка. Требует регистрации на developers.sber.ru.",
    ))

    # T-Bank VoiceKit
    tbank_voices = [
        VoiceOption(id=vid, name=info["desc"], gender=info["gender"])
        for vid, info in TBANK_VOICES.items()
    ]
    providers.append(ProviderInfo(
        provider="tbank_voicekit",
        display_name="T-Bank VoiceKit",
        enabled=settings.tbank_voicekit_enabled,
        configured=settings.tbank_voicekit_configured,
        voices=tbank_voices,
        note="REST API. Полный доступ требует корпоративного аккаунта T-Bank.",
    ))

    # ElevenLabs
    providers.append(ProviderInfo(
        provider="elevenlabs",
        display_name="ElevenLabs",
        enabled=settings.elevenlabs_enabled,
        configured=settings.elevenlabs_configured,
        voices=[VoiceOption(
            id=settings.elevenlabs_voice_id or "",
            name="Текущий клонированный голос",
            description="Настраивается в разделе Провайдеры → ElevenLabs",
        )] if settings.elevenlabs_voice_id else [],
        note="Клон голоса ~1–1.5 сек. Голос задаётся глобально через провайдер.",
    ))

    # Gemini (special — no TTS provider, native audio)
    providers.append(ProviderInfo(
        provider="gemini_native",
        display_name="Gemini Native Audio",
        enabled=settings.gemini_configured,
        configured=settings.gemini_configured,
        voices=[
            VoiceOption(id="Aoede",     name="Aoede",     gender="female", description="Женский, мягкий"),
            VoiceOption(id="Kore",      name="Kore",      gender="female", description="Женский, чёткий"),
            VoiceOption(id="Sulafat",   name="Sulafat",   gender="female", description="Женский, нейтральный"),
            VoiceOption(id="Fenrir",    name="Fenrir",    gender="male",   description="Мужской, уверенный"),
            VoiceOption(id="Charon",    name="Charon",    gender="male",   description="Мужской, спокойный"),
            VoiceOption(id="Puck",      name="Puck",      gender="male",   description="Мужской, живой"),
        ],
        note="Нативный аудио Gemini. Не требует отдельного TTS провайдера — стратегия gemini_primary.",
    ))

    return providers


async def _synthesize_preview(provider: str, voice_id: str, text: str, emotion: Optional[str]) -> bytes:
    """Call the appropriate TTS provider and return raw PCM bytes."""
    full_voice_id = f"{voice_id}:{emotion}" if emotion else voice_id

    if provider == "cartesia":
        from app.integrations.voice.cartesia import CartesiaClient
        client = CartesiaClient(
            api_key=settings.cartesia_api_key,
            default_voice_id=settings.cartesia_voice_id or voice_id,
            config_source="preview",
        )
        return await client.synthesize(text, voice_id=voice_id)

    if provider == "yandex_speechkit":
        from app.integrations.voice.yandex_speechkit import YandexSpeechKitClient
        client = YandexSpeechKitClient(
            api_key=settings.yandex_speechkit_api_key,
            folder_id=settings.yandex_speechkit_folder_id,
            config_source="preview",
        )
        return await client.synthesize(text, voice_id=full_voice_id)

    if provider == "sber_salutespeech":
        from app.integrations.voice.sber_salutespeech import SberSaluteSpeechClient
        client = SberSaluteSpeechClient(
            client_id=settings.sber_salutespeech_client_id,
            client_secret=settings.sber_salutespeech_client_secret,
            scope=settings.sber_salutespeech_scope,
            config_source="preview",
        )
        return await client.synthesize(text, voice_id=full_voice_id)

    if provider == "tbank_voicekit":
        from app.integrations.voice.tbank_voicekit import TBankVoiceKitClient
        client = TBankVoiceKitClient(
            api_key=settings.tbank_voicekit_api_key,
            secret_key=settings.tbank_voicekit_secret_key,
            endpoint=settings.tbank_voicekit_endpoint,
            config_source="preview",
        )
        return await client.synthesize(text, voice_id=full_voice_id)

    if provider == "elevenlabs":
        from app.integrations.voice.elevenlabs import ElevenLabsClient
        client = ElevenLabsClient(
            api_key=settings.elevenlabs_api_key,
            default_voice_id=voice_id or settings.elevenlabs_voice_id,
            config_source="preview",
        )
        return await client.synthesize(text, voice_id=voice_id or None)

    raise HTTPException(status_code=400, detail=f"Provider '{provider}' does not support TTS preview or is not configured")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/providers", response_model=list[ProviderInfo])
async def list_tts_providers() -> list[ProviderInfo]:
    """List all TTS providers with voices and configuration status."""
    return _build_provider_list()


@router.post("/preview", response_model=PreviewResponse)
async def generate_tts_preview(body: PreviewRequest) -> PreviewResponse:
    """
    Generate a TTS audio preview.
    Returns base64-encoded WAV file playable directly in browser <audio> tag.
    Results are cached for 5 minutes.
    """
    if len(body.text) > _PREVIEW_MAX_TEXT_LEN:
        raise HTTPException(status_code=400, detail=f"Text too long (max {_PREVIEW_MAX_TEXT_LEN} chars)")

    cache_k = _cache_key(body.provider, body.voice_id, body.text + (body.emotion or ""))
    cached_wav = _cache_get(cache_k)
    if cached_wav:
        return PreviewResponse(
            provider=body.provider,
            voice_id=body.voice_id,
            wav_base64=base64.b64encode(cached_wav).decode(),
            duration_ms=None,
            latency_ms=0.0,
            cached=True,
            byte_size=len(cached_wav),
        )

    t0 = time.monotonic()
    try:
        pcm = await _synthesize_preview(body.provider, body.voice_id, body.text, body.emotion)
    except EngineError as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"error": str(exc)}
        raise HTTPException(status_code=502, detail=detail) from exc
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("tts_preview.error", provider=body.provider, voice_id=body.voice_id)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    latency_ms = (time.monotonic() - t0) * 1000
    wav = _pcm_to_wav(pcm)
    _cache_put(cache_k, wav)

    log.info(
        "tts_preview.generated",
        provider=body.provider,
        voice_id=body.voice_id,
        latency_ms=round(latency_ms),
        byte_size=len(wav),
    )

    return PreviewResponse(
        provider=body.provider,
        voice_id=body.voice_id,
        wav_base64=base64.b64encode(wav).decode(),
        duration_ms=round(len(pcm) / (16000 * 2) * 1000, 1),  # PCM16 @ 16kHz
        latency_ms=round(latency_ms, 1),
        cached=False,
        byte_size=len(wav),
    )

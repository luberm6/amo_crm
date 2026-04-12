from __future__ import annotations

import pytest

from app.core.config import settings
from app.core.exceptions import EngineError
from app.integrations.direct.voice_strategy import (
    ensure_voice_strategy_valid,
    make_session_voice_state,
)


def _save_settings() -> dict[str, object]:
    return {
        "direct_voice_strategy": settings.direct_voice_strategy,
        "direct_voice_allow_tts_fallback": settings.direct_voice_allow_tts_fallback,
        "gemini_audio_output_enabled": settings.gemini_audio_output_enabled,
        "elevenlabs_enabled": settings.elevenlabs_enabled,
        "elevenlabs_api_key": settings.elevenlabs_api_key,
        "elevenlabs_voice_id": settings.elevenlabs_voice_id,
    }


def _restore_settings(old: dict[str, object]) -> None:
    for key, value in old.items():
        setattr(settings, key, value)


def test_tts_primary_warns_when_gemini_audio_output_enabled() -> None:
    # tts_primary + GEMINI_AUDIO_OUTPUT_ENABLED=true is now a warning, not an error.
    # tts_primary uses AUDIO modality (required by gemini-3.1-flash-live-preview) but
    # discards Gemini's audio and pipes the outputAudioTranscription to ElevenLabs instead.
    old = _save_settings()
    try:
        settings.direct_voice_strategy = "tts_primary"
        settings.gemini_audio_output_enabled = True
        settings.elevenlabs_enabled = True
        settings.elevenlabs_api_key = "k"
        settings.elevenlabs_voice_id = "v"

        # Should not raise — validation passes with a warning
        definition = ensure_voice_strategy_valid()
        assert definition.primary_path == "tts_primary"
    finally:
        _restore_settings(old)


def test_gemini_primary_allows_explicit_tts_fallback() -> None:
    old = _save_settings()
    try:
        settings.direct_voice_strategy = "gemini_primary"
        settings.direct_voice_allow_tts_fallback = True
        settings.gemini_audio_output_enabled = True
        settings.elevenlabs_enabled = True
        settings.elevenlabs_api_key = "k"
        settings.elevenlabs_voice_id = "v"

        definition = ensure_voice_strategy_valid()

        assert definition.primary_path == "gemini_native"
        assert definition.fallback_path == "tts_fallback"
    finally:
        _restore_settings(old)


def test_make_session_voice_state_uses_tts_primary_for_greeting_and_dialog() -> None:
    old = _save_settings()
    try:
        settings.direct_voice_strategy = "tts_primary"
        settings.gemini_audio_output_enabled = False
        settings.elevenlabs_enabled = True
        settings.elevenlabs_api_key = "k"
        settings.elevenlabs_voice_id = "v"

        state = make_session_voice_state()

        assert state.primary_path == "tts_primary"
        assert state.initial_greeting_path == "tts_primary"
        assert state.active_path == "tts_primary"
        assert state.wants_tts_output() is True
        assert state.wants_gemini_audio_output() is False
    finally:
        _restore_settings(old)

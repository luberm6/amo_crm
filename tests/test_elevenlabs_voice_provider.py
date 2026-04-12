from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest

from app.api import deps
from app.integrations.voice.elevenlabs import ElevenLabsClient
from app.integrations.voice.stub import StubVoiceProvider
from app.core.exceptions import EngineError
from app.core.config import settings


@pytest.mark.anyio
async def test_elevenlabs_synthesize_uses_pcm_query_param_and_audio_payload() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["query"] = dict(request.url.params)
        captured["headers"] = dict(request.headers)
        captured["json"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            headers={"content-type": "audio/pcm"},
            content=b"\x01\x02" * 640,
            request=request,
        )

    client = ElevenLabsClient(
        api_key="el-key",
        default_voice_id="voice-123",
        enabled=True,
        config_source="test",
        transport=httpx.MockTransport(handler),
    )

    pcm = await client.synthesize("Привет, мир")

    assert pcm == b"\x01\x02" * 640
    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/text-to-speech/voice-123"
    assert captured["query"] == {"output_format": "pcm_16000"}
    assert captured["json"] == {
        "text": "Привет, мир",
        "model_id": "eleven_flash_v2_5",
    }
    assert captured["headers"]["accept"] == "audio/pcm"
    assert "output_format" not in captured["json"]


@pytest.mark.anyio
async def test_elevenlabs_streaming_rejects_non_audio_content_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b'{"ok": true}',
            request=request,
        )

    client = ElevenLabsClient(
        api_key="el-key",
        default_voice_id="voice-123",
        enabled=True,
        config_source="test",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(EngineError) as exc_info:
        async for _ in client.synthesize_streaming("Тест"):
            pass

    detail = exc_info.value.detail
    assert detail["provider"] == "elevenlabs"
    assert detail["stage"] == "response_parse"
    assert detail["content_type"] == "application/json"


@pytest.mark.anyio
async def test_elevenlabs_missing_voice_id_fails_at_provider_resolve() -> None:
    old_enabled = settings.elevenlabs_enabled
    old_key = settings.elevenlabs_api_key
    old_voice = settings.elevenlabs_voice_id
    try:
        settings.elevenlabs_enabled = True
        settings.elevenlabs_api_key = ""
        settings.elevenlabs_voice_id = ""
        client = ElevenLabsClient(
            api_key="el-key",
            default_voice_id="",
            enabled=True,
            config_source="test",
        )

        with pytest.raises(EngineError) as exc_info:
            await client.synthesize("Тест")

        detail = exc_info.value.detail
        assert detail["provider"] == "elevenlabs"
        assert detail["stage"] == "provider_resolve"
        assert detail["voice_id_source"] == "env"
    finally:
        settings.elevenlabs_enabled = old_enabled
        settings.elevenlabs_api_key = old_key
        settings.elevenlabs_voice_id = old_voice


def test_voice_provider_resolution_is_not_stale_across_setting_changes() -> None:
    old_enabled = settings.elevenlabs_enabled
    old_key = settings.elevenlabs_api_key
    old_voice = settings.elevenlabs_voice_id
    try:
        settings.elevenlabs_enabled = False
        settings.elevenlabs_api_key = ""
        settings.elevenlabs_voice_id = ""
        provider = deps._build_voice_provider()
        assert isinstance(provider, StubVoiceProvider)

        settings.elevenlabs_enabled = True
        settings.elevenlabs_api_key = "el-key"
        settings.elevenlabs_voice_id = "voice-123"
        provider = deps._build_voice_provider()
        assert isinstance(provider, ElevenLabsClient)
        diagnostics = provider.runtime_diagnostics()
        assert diagnostics["config_source"] == "env"
        assert diagnostics["api_key_set"] is True
    finally:
        settings.elevenlabs_enabled = old_enabled
        settings.elevenlabs_api_key = old_key
        settings.elevenlabs_voice_id = old_voice


def test_runtime_diagnostics_reports_constructor_voice_source() -> None:
    client = ElevenLabsClient(
        api_key="el-key",
        default_voice_id="voice-123",
        enabled=True,
        config_source="provider_settings",
    )

    diagnostics = client.runtime_diagnostics()

    assert diagnostics["provider"] == "elevenlabs"
    assert diagnostics["config_source"] == "provider_settings"
    assert diagnostics["voice_id_source"] == "constructor"
    assert diagnostics["api_key_set"] is True

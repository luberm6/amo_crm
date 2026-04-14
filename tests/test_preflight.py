from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.services.preflight_service import DirectVoicePreflightService


class _FakeGateway:
    async def _ensure_esl_connected(self) -> None:
        return None

    async def health(self) -> dict:
        return {
            "provider": "freeswitch",
            "mode": "esl_rtp",
            "active_sessions": 0,
            "esl_connected": True,
        }


def _set_direct_voice_settings() -> dict[str, object]:
    old = {
        "environment": settings.environment,
        "backend_url": settings.backend_url,
        "render_external_url": settings.render_external_url,
        "telephony_provider": settings.telephony_provider,
        "gemini_api_key": settings.gemini_api_key,
        "mango_api_key": settings.mango_api_key,
        "mango_api_salt": settings.mango_api_salt,
        "mango_from_ext": settings.mango_from_ext,
        "media_gateway_enabled": settings.media_gateway_enabled,
        "media_gateway_provider": settings.media_gateway_provider,
        "media_gateway_mode": settings.media_gateway_mode,
        "direct_voice_strategy": settings.direct_voice_strategy,
        "direct_voice_allow_tts_fallback": settings.direct_voice_allow_tts_fallback,
        "gemini_audio_output_enabled": settings.gemini_audio_output_enabled,
        "elevenlabs_enabled": settings.elevenlabs_enabled,
        "elevenlabs_api_key": settings.elevenlabs_api_key,
        "elevenlabs_voice_id": settings.elevenlabs_voice_id,
        "direct_initial_greeting_enabled": settings.direct_initial_greeting_enabled,
        "direct_model_response_timeout_seconds": settings.direct_model_response_timeout_seconds,
    }
    settings.environment = "production"
    settings.backend_url = "https://voice.example.com"
    settings.render_external_url = ""
    settings.telephony_provider = "mango"
    settings.gemini_api_key = "gemini-key"
    settings.mango_api_key = "mango-key"
    settings.mango_api_salt = "mango-salt"
    settings.mango_from_ext = "101"
    settings.media_gateway_enabled = True
    settings.media_gateway_provider = "freeswitch"
    settings.media_gateway_mode = "esl_rtp"
    settings.direct_voice_strategy = "gemini_primary"
    settings.direct_voice_allow_tts_fallback = True
    settings.gemini_audio_output_enabled = True
    settings.elevenlabs_enabled = True
    settings.elevenlabs_api_key = "el-key"
    settings.elevenlabs_voice_id = "voice"
    settings.direct_initial_greeting_enabled = True
    settings.direct_model_response_timeout_seconds = 8.0
    return old


def _restore_settings(old: dict[str, object]) -> None:
    for key, value in old.items():
        setattr(settings, key, value)


@pytest.mark.anyio
async def test_direct_voice_preflight_passes_with_valid_contour(session, monkeypatch):
    old = _set_direct_voice_settings()
    try:
        service = DirectVoicePreflightService(session)

        async def _db_ok(checks):
            service._add_check(checks, "database", "pass", "Database is reachable.")

        async def _redis_ok(checks):
            service._add_check(checks, "redis", "pass", "Redis is reachable.")

        monkeypatch.setattr(service, "_check_database", _db_ok)
        monkeypatch.setattr(service, "_check_redis", _redis_ok)
        monkeypatch.setattr(
            service,
            "_resolve_telephony_adapter",
            lambda checks: SimpleNamespace(
                capabilities=SimpleNamespace(
                    supports_outbound_call=True,
                    supports_audio_bridge=True,
                )
            ),
        )
        monkeypatch.setattr(
            "app.services.preflight_service.get_media_gateway",
            lambda: _FakeGateway(),
        )

        payload = await service.run()

        assert payload["status"] == "pass"
        assert payload["summary"]["failed"] == 0
    finally:
        _restore_settings(old)


@pytest.mark.anyio
async def test_direct_voice_preflight_fails_without_outbound_voice_path(session, monkeypatch):
    old = _set_direct_voice_settings()
    try:
        settings.direct_voice_strategy = "gemini_primary"
        settings.gemini_audio_output_enabled = False
        settings.elevenlabs_enabled = False
        settings.elevenlabs_api_key = ""
        settings.elevenlabs_voice_id = ""

        service = DirectVoicePreflightService(session)

        async def _db_ok(checks):
            service._add_check(checks, "database", "pass", "Database is reachable.")

        async def _redis_ok(checks):
            service._add_check(checks, "redis", "pass", "Redis is reachable.")

        monkeypatch.setattr(service, "_check_database", _db_ok)
        monkeypatch.setattr(service, "_check_redis", _redis_ok)
        monkeypatch.setattr(
            service,
            "_resolve_telephony_adapter",
            lambda checks: SimpleNamespace(
                capabilities=SimpleNamespace(
                    supports_outbound_call=True,
                    supports_audio_bridge=True,
                )
            ),
        )
        monkeypatch.setattr(
            "app.services.preflight_service.get_media_gateway",
            lambda: _FakeGateway(),
        )

        payload = await service.run()

        assert payload["status"] == "fail"
        assert any(
            c["name"] == "voice_strategy_gemini_output" and c["status"] == "fail"
            for c in payload["checks"]
        )
    finally:
        _restore_settings(old)


def test_effective_backend_url_prefers_render_public_url_when_local_backend_url():
    old_backend_url = settings.backend_url
    old_render_external_url = settings.render_external_url
    try:
        settings.backend_url = "http://127.0.0.1:8000"
        settings.render_external_url = "https://amo-crm-api.onrender.com"
        assert settings.effective_backend_url == "https://amo-crm-api.onrender.com"
    finally:
        settings.backend_url = old_backend_url
        settings.render_external_url = old_render_external_url


@pytest.mark.anyio
async def test_direct_voice_preflight_endpoint_returns_payload(client, monkeypatch):
    async def _fake_run(self):
        return {
            "status": "pass",
            "target": "direct_voice_first_call",
            "summary": {"passed": 1, "warnings": 0, "failed": 0, "total": 1},
            "checks": [{"name": "demo", "status": "pass", "message": "ok"}],
        }

    monkeypatch.setattr("app.api.v1.health.DirectVoicePreflightService.run", _fake_run)
    resp = await client.get("/v1/preflight/direct-voice")
    assert resp.status_code == 200
    assert resp.json()["status"] == "pass"


@pytest.mark.anyio
async def test_direct_voice_preflight_fails_when_auto_resolves_to_stub(session, monkeypatch):
    old = _set_direct_voice_settings()
    try:
        settings.telephony_provider = "auto"
        settings.mango_api_key = ""
        settings.mango_api_salt = ""

        service = DirectVoicePreflightService(session)

        async def _db_ok(checks):
            service._add_check(checks, "database", "pass", "Database is reachable.")

        async def _redis_ok(checks):
            service._add_check(checks, "redis", "pass", "Redis is reachable.")

        monkeypatch.setattr(service, "_check_database", _db_ok)
        monkeypatch.setattr(service, "_check_redis", _redis_ok)
        monkeypatch.setattr(
            "app.services.preflight_service.get_media_gateway",
            lambda: _FakeGateway(),
        )

        payload = await service.run()

        assert payload["status"] == "fail"
        telephony_resolution = next(
            c for c in payload["checks"] if c["name"] == "telephony_resolution"
        )
        assert telephony_resolution["status"] == "fail"
        details = telephony_resolution.get("details", {})
        assert (
            details.get("resolved_provider") == "stub"
            or "StubTelephonyAdapter is NOT allowed in production."
            in details.get("error", "")
        )
    finally:
        _restore_settings(old)


@pytest.mark.anyio
async def test_direct_voice_preflight_rejects_invalid_hybrid_under_tts_primary(session, monkeypatch):
    old = _set_direct_voice_settings()
    try:
        settings.direct_voice_strategy = "tts_primary"
        settings.gemini_audio_output_enabled = True

        service = DirectVoicePreflightService(session)

        async def _db_ok(checks):
            service._add_check(checks, "database", "pass", "Database is reachable.")

        async def _redis_ok(checks):
            service._add_check(checks, "redis", "pass", "Redis is reachable.")

        monkeypatch.setattr(service, "_check_database", _db_ok)
        monkeypatch.setattr(service, "_check_redis", _redis_ok)
        monkeypatch.setattr(
            service,
            "_resolve_telephony_adapter",
            lambda checks: SimpleNamespace(
                capabilities=SimpleNamespace(
                    supports_outbound_call=True,
                    supports_audio_bridge=True,
                )
            ),
        )
        monkeypatch.setattr(
            "app.services.preflight_service.get_media_gateway",
            lambda: _FakeGateway(),
        )

        payload = await service.run()

        # tts_primary + gemini_audio_output_enabled=True now produces a warning, not a failure
        assert payload["status"] == "warn"
        assert any(
            c["name"] == "voice_strategy_hybrid_guard" and c["status"] == "warn"
            for c in payload["checks"]
        )
    finally:
        _restore_settings(old)


@pytest.mark.anyio
async def test_direct_voice_preflight_warns_when_from_ext_auto_discoverable(session, monkeypatch):
    old = _set_direct_voice_settings()
    try:
        settings.mango_from_ext = ""

        service = DirectVoicePreflightService(session)

        async def _db_ok(checks):
            service._add_check(checks, "database", "pass", "Database is reachable.")

        async def _redis_ok(checks):
            service._add_check(checks, "redis", "pass", "Redis is reachable.")

        monkeypatch.setattr(service, "_check_database", _db_ok)
        monkeypatch.setattr(service, "_check_redis", _redis_ok)
        monkeypatch.setattr(
            service,
            "_resolve_telephony_adapter",
            lambda checks: SimpleNamespace(
                capabilities=SimpleNamespace(
                    supports_outbound_call=True,
                    supports_audio_bridge=True,
                )
            ),
        )
        monkeypatch.setattr(
            "app.services.preflight_service.resolve_mango_from_ext",
            AsyncMock(
                return_value=SimpleNamespace(
                    value="10",
                    source="auto_discovered_first_extension",
                    candidate_count=2,
                    matched_line_id=None,
                    matched_line_phone_number="+79585382099",
                )
            ),
        )
        monkeypatch.setattr(
            "app.services.preflight_service.get_media_gateway",
            lambda: _FakeGateway(),
        )

        payload = await service.run()

        mango_from_ext = next(c for c in payload["checks"] if c["name"] == "mango_from_ext")
        assert mango_from_ext["status"] == "warn"
        assert mango_from_ext["details"]["resolved_from_ext"] == "10"
        assert mango_from_ext["details"]["source"] == "auto_discovered_first_extension"
    finally:
        _restore_settings(old)

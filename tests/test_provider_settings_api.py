from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

import app.core.config as cfg
from app.db.session import get_db
from app.integrations.voice.elevenlabs import ElevenLabsClient
from app.main import create_app
from app.services.provider_settings_service import ProviderSettingsService


@pytest.fixture
def admin_and_provider_settings():
    with (
        patch.object(cfg.settings, "admin_email", "admin@example.com"),
        patch.object(cfg.settings, "admin_password", "super-secret"),
        patch.object(cfg.settings, "admin_auth_secret", "signing-secret"),
        patch.object(cfg.settings, "provider_settings_secret", "provider-secret"),
        patch.object(cfg.settings, "admin_token_ttl_seconds", 600),
    ):
        yield


async def _login(ac: AsyncClient) -> str:
    response = await ac.post(
        "/v1/admin/auth/login",
        json={"email": "admin@example.com", "password": "super-secret"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


@pytest.mark.anyio
async def test_save_provider_settings_masks_secrets(session, admin_and_provider_settings):
    app = create_app()

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token = await _login(ac)
        save_response = await ac.patch(
            "/v1/providers/settings/mango",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "is_enabled": False,
                "config": {"from_ext": "101", "webhook_ip_allowlist": "1.1.1.1/32"},
                "secrets": {"api_key": "mango-secret-key", "api_salt": "mango-secret-salt"},
            },
        )
        list_response = await ac.get(
            "/v1/providers/settings",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert save_response.status_code == 200
    payload = save_response.json()
    assert payload["provider"] == "mango"
    assert payload["status"] == "not_tested"
    assert payload["activation_status"] == "inactive"
    assert payload["config"]["from_ext"] == "101"
    assert payload["secrets"]["api_key"]["is_set"] is True
    assert payload["secrets"]["api_key"]["masked_value"] != "mango-secret-key"
    assert "mango-secret-key" not in save_response.text
    assert list_response.status_code == 200
    mango = next(item for item in list_response.json()["items"] if item["provider"] == "mango")
    assert mango["secrets"]["api_salt"]["is_set"] is True
    assert "mango-secret-salt" not in list_response.text


@pytest.mark.anyio
async def test_validate_mango_settings_stays_in_safe_mode(session, admin_and_provider_settings):
    app = create_app()

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token = await _login(ac)
        save_response = await ac.patch(
            "/v1/providers/settings/mango",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "is_enabled": True,
                "config": {"from_ext": "101"},
                "secrets": {"api_key": "mango-secret-key", "api_salt": "mango-secret-salt"},
            },
        )
        assert save_response.status_code == 200

        validate_response = await ac.post(
            "/v1/providers/settings/mango/validate",
            headers={"Authorization": f"Bearer {token}"},
        )
        list_response = await ac.get(
            "/v1/providers/settings",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert validate_response.status_code == 200
    payload = validate_response.json()
    assert payload["status"] == "configured"
    assert payload["remote_checked"] is False
    assert "No number sync" in payload["message"]
    mango = next(item for item in list_response.json()["items"] if item["provider"] == "mango")
    assert mango["status"] == "configured"
    assert mango["activation_status"] == "active"


@pytest.mark.anyio
async def test_validate_gemini_settings_uses_remote_check(session, admin_and_provider_settings):
    app = create_app()

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db

    async def fake_validate(self, config, secrets):
        assert config["model_id"] == "gemini-2.0-flash-live-001"
        assert secrets["api_key"] == "gemini-secret-key"
        return None

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token = await _login(ac)
        save_response = await ac.patch(
            "/v1/providers/settings/gemini",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "is_enabled": True,
                "config": {"model_id": "gemini-2.0-flash-live-001", "api_version": "v1beta"},
                "secrets": {"api_key": "gemini-secret-key"},
            },
        )
        assert save_response.status_code == 200
        with patch.object(ProviderSettingsService, "_validate_gemini", new=fake_validate):
            validate_response = await ac.post(
                "/v1/providers/settings/gemini/validate",
                headers={"Authorization": f"Bearer {token}"},
            )

    assert validate_response.status_code == 200
    payload = validate_response.json()
    assert payload["status"] == "configured"
    assert payload["remote_checked"] is True
    assert payload["message"] == "Gemini model settings responded successfully."


@pytest.mark.anyio
async def test_validate_elevenlabs_settings_uses_runtime_tts_contract(
    session, admin_and_provider_settings
):
    app = create_app()

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db

    async def fake_validate(self):
        diagnostics = self.runtime_diagnostics()
        assert diagnostics["provider"] == "elevenlabs"
        assert diagnostics["config_source"] == "provider_settings"
        assert diagnostics["api_key_set"] is True
        assert diagnostics["voice_id_source"] == "constructor"
        return b"\x01\x02" * 320

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token = await _login(ac)
        save_response = await ac.patch(
            "/v1/providers/settings/elevenlabs",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "is_enabled": True,
                "config": {"voice_id": "voice-123"},
                "secrets": {"api_key": "elevenlabs-secret-key"},
            },
        )
        assert save_response.status_code == 200
        with patch.object(ElevenLabsClient, "validate_tts_contract", new=fake_validate):
            validate_response = await ac.post(
                "/v1/providers/settings/elevenlabs/validate",
                headers={"Authorization": f"Bearer {token}"},
            )

    assert validate_response.status_code == 200
    payload = validate_response.json()
    assert payload["status"] == "configured"
    assert payload["remote_checked"] is True
    assert payload["message"] == "ElevenLabs voice settings responded successfully."


@pytest.mark.anyio
async def test_save_provider_settings_returns_structured_500_for_unexpected_errors(
    session, admin_and_provider_settings
):
    app = create_app()

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db

    async def boom(*args, **kwargs):
        raise RuntimeError("boom-save")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token = await _login(ac)
        with patch.object(ProviderSettingsService, "update_provider", new=boom):
            save_response = await ac.patch(
                "/v1/providers/settings/gemini",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "is_enabled": True,
                    "config": {"model_id": "gemini-2.0-flash-live-001", "api_version": "v1beta"},
                    "secrets": {"api_key": "gemini-secret-key"},
                },
            )

    assert save_response.status_code == 500
    assert save_response.json()["detail"] == {
        "error": "provider_settings_save_failed",
        "message": "boom-save",
        "provider": "gemini",
    }


@pytest.mark.anyio
async def test_list_provider_settings_survives_secret_rotation(session):
    with (
        patch.object(cfg.settings, "provider_settings_secret", "old-provider-secret"),
        patch.object(cfg.settings, "admin_auth_secret", "signing-secret"),
    ):
        service = ProviderSettingsService(session)
        await service.update_provider(
            "gemini",
            is_enabled=True,
            config={"model_id": "gemini-2.0-flash-live-001", "api_version": "v1beta"},
            secrets={"api_key": "legacy-gemini-secret"},
        )
        await session.commit()

    app = create_app()

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db

    with (
        patch.object(cfg.settings, "admin_email", "admin@example.com"),
        patch.object(cfg.settings, "admin_password", "super-secret"),
        patch.object(cfg.settings, "admin_auth_secret", "signing-secret"),
        patch.object(cfg.settings, "provider_settings_secret", "new-provider-secret"),
        patch.object(cfg.settings, "admin_token_ttl_seconds", 600),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            token = await _login(ac)
            response = await ac.get(
                "/v1/providers/settings",
                headers={"Authorization": f"Bearer {token}"},
            )

    assert response.status_code == 200
    gemini = next(item for item in response.json()["items"] if item["provider"] == "gemini")
    assert gemini["status"] == "invalid"
    assert gemini["secrets_accessible"] is False
    assert "encrypted with a different secret" in gemini["storage_warning"]


@pytest.mark.anyio
async def test_save_provider_settings_can_reencrypt_after_secret_rotation(session):
    with (
        patch.object(cfg.settings, "provider_settings_secret", "old-provider-secret"),
        patch.object(cfg.settings, "admin_auth_secret", "signing-secret"),
    ):
        service = ProviderSettingsService(session)
        await service.update_provider(
            "gemini",
            is_enabled=True,
            config={"model_id": "gemini-2.0-flash-live-001", "api_version": "v1beta"},
            secrets={"api_key": "legacy-gemini-secret"},
        )
        await session.commit()

    app = create_app()

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db

    with (
        patch.object(cfg.settings, "admin_email", "admin@example.com"),
        patch.object(cfg.settings, "admin_password", "super-secret"),
        patch.object(cfg.settings, "admin_auth_secret", "signing-secret"),
        patch.object(cfg.settings, "provider_settings_secret", "new-provider-secret"),
        patch.object(cfg.settings, "admin_token_ttl_seconds", 600),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            token = await _login(ac)
            save_response = await ac.patch(
                "/v1/providers/settings/gemini",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "is_enabled": True,
                    "config": {"model_id": "gemini-2.0-flash-live-001", "api_version": "v1beta"},
                    "secrets": {"api_key": "reencrypted-gemini-secret"},
                },
            )
            list_response = await ac.get(
                "/v1/providers/settings",
                headers={"Authorization": f"Bearer {token}"},
            )

    assert save_response.status_code == 200
    assert list_response.status_code == 200
    gemini = next(item for item in list_response.json()["items"] if item["provider"] == "gemini")
    assert gemini["secrets_accessible"] is True
    assert gemini["storage_warning"] is None
    assert gemini["secrets"]["api_key"]["is_set"] is True

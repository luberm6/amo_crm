from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

import app.core.config as cfg
from app.db.session import get_db
from app.main import create_app


@pytest.fixture
def admin_auth_settings():
    with (
        patch.object(cfg.settings, "admin_email", "admin@example.com"),
        patch.object(cfg.settings, "admin_password", "super-secret"),
        patch.object(cfg.settings, "admin_auth_secret", "signing-secret"),
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
async def test_create_agent_profile(session, admin_auth_settings):
    app = create_app()

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token = await _login(ac)
        response = await ac.post(
            "/v1/agents",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "Sales Alpha",
                "is_active": True,
                "system_prompt": "Ты продающий агент.",
                "tone_rules": "Говори спокойно.",
                "business_rules": "Не обещай скидку без одобрения.",
                "sales_objectives": "Назначить демо.",
                "greeting_text": "Здравствуйте! Чем могу помочь?",
                "transfer_rules": "Передавай менеджеру только после запроса.",
                "prohibited_promises": "Не обещай возврат денег.",
                "voice_strategy": "tts_primary",
                "config": {"locale": "ru-RU"},
            },
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["name"] == "Sales Alpha"
    assert payload["version"] == 1
    assert "System Prompt:" in payload["assembled_prompt_preview"]
    assert "Tone Rules:" in payload["assembled_prompt_preview"]


@pytest.mark.anyio
async def test_update_agent_profile(session, admin_auth_settings):
    app = create_app()

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token = await _login(ac)
        create_response = await ac.post(
            "/v1/agents",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "Sales Beta",
                "is_active": True,
                "system_prompt": "Базовый промт.",
                "voice_strategy": "tts_primary",
                "config": {},
            },
        )
        agent_id = create_response.json()["id"]

        update_response = await ac.patch(
            f"/v1/agents/{agent_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "sales_objectives": "Получить согласие на следующий шаг.",
                "voice_strategy": "gemini_primary",
                "is_active": False,
            },
        )

    assert update_response.status_code == 200
    payload = update_response.json()
    assert payload["version"] == 2
    assert payload["voice_strategy"] == "gemini_primary"
    assert payload["is_active"] is False
    assert "Sales Objectives:" in payload["assembled_prompt_preview"]


@pytest.mark.anyio
async def test_fetch_agent_list(session, admin_auth_settings):
    app = create_app()

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token = await _login(ac)
        initial_list_response = await ac.get(
            "/v1/agents",
            headers={"Authorization": f"Bearer {token}"},
        )
        initial_active_only_response = await ac.get(
            "/v1/agents?active_only=true",
            headers={"Authorization": f"Bearer {token}"},
        )
        initial_total = initial_list_response.json()["total"]
        initial_active_total = initial_active_only_response.json()["total"]

        for index in range(2):
            response = await ac.post(
                "/v1/agents",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "name": f"Agent {index}",
                    "is_active": index == 0,
                    "system_prompt": "Рабочий промт.",
                    "voice_strategy": "tts_primary",
                    "config": {},
                },
            )
            assert response.status_code == 201

        list_response = await ac.get(
            "/v1/agents",
            headers={"Authorization": f"Bearer {token}"},
        )
        active_only_response = await ac.get(
            "/v1/agents?active_only=true",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert list_response.status_code == 200
    assert list_response.json()["total"] == initial_total + 2
    assert active_only_response.status_code == 200
    assert active_only_response.json()["total"] == initial_active_total + 1


@pytest.mark.anyio
async def test_agent_validation(session, admin_auth_settings):
    app = create_app()

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token = await _login(ac)
        response = await ac.post(
            "/v1/agents",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "Broken agent",
                "is_active": True,
                "system_prompt": "Тест",
                "voice_strategy": "unsupported_strategy",
                "config": {},
            },
        )

    assert response.status_code == 422

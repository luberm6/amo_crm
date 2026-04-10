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
async def test_create_and_update_knowledge_document(session, admin_auth_settings):
    app = create_app()

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token = await _login(ac)
        create_response = await ac.post(
            "/v1/knowledge-documents",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "title": "Main tariff",
                "category": "pricing",
                "content": "Base package starts at 30 000 RUB.",
                "is_active": True,
                "notes": "Reviewed by sales ops",
                "metadata": {"currency": "RUB"},
            },
        )
        document_id = create_response.json()["id"]
        update_response = await ac.patch(
            f"/v1/knowledge-documents/{document_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "content": "Base package starts at 35 000 RUB.",
                "is_active": False,
                "metadata": {"currency": "RUB", "tier": "base"},
            },
        )

    assert create_response.status_code == 201
    assert create_response.json()["category"] == "pricing"
    assert update_response.status_code == 200
    assert update_response.json()["content"] == "Base package starts at 35 000 RUB."
    assert update_response.json()["is_active"] is False
    assert update_response.json()["metadata"]["tier"] == "base"


@pytest.mark.anyio
async def test_list_knowledge_documents_by_category(session, admin_auth_settings):
    app = create_app()

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token = await _login(ac)
        for payload in (
            {
                "title": "FAQ 1",
                "category": "faq",
                "content": "Answer to frequent question.",
                "is_active": True,
                "metadata": {},
            },
            {
                "title": "Service sheet",
                "category": "services",
                "content": "We provide onboarding.",
                "is_active": True,
                "metadata": {},
            },
        ):
            response = await ac.post(
                "/v1/knowledge-documents",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
            assert response.status_code == 201

        list_response = await ac.get(
            "/v1/knowledge-documents?category=faq&active_only=true",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert list_response.status_code == 200
    payload = list_response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["category"] == "faq"
    assert payload["items"][0]["title"] == "FAQ 1"


@pytest.mark.anyio
async def test_bind_and_unbind_knowledge_document_to_agent(session, admin_auth_settings):
    app = create_app()

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token = await _login(ac)
        agent_response = await ac.post(
            "/v1/agents",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "KB Agent",
                "is_active": True,
                "system_prompt": "Use controlled context.",
                "voice_strategy": "tts_primary",
                "config": {},
            },
        )
        document_response = await ac.post(
            "/v1/knowledge-documents",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "title": "Refund policy",
                "category": "company_policy",
                "content": "Refunds require approval within 14 days.",
                "is_active": True,
                "metadata": {},
            },
        )
        agent_id = agent_response.json()["id"]
        document_id = document_response.json()["id"]

        bind_response = await ac.post(
            f"/v1/agents/{agent_id}/knowledge/bind",
            headers={"Authorization": f"Bearer {token}"},
            json={"knowledge_document_id": document_id, "role": "policy"},
        )
        bindings_response = await ac.get(
            f"/v1/agents/{agent_id}/knowledge",
            headers={"Authorization": f"Bearer {token}"},
        )
        binding_id = bind_response.json()["id"]
        unbind_response = await ac.delete(
            f"/v1/agents/{agent_id}/knowledge/{binding_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        bindings_after_unbind = await ac.get(
            f"/v1/agents/{agent_id}/knowledge",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert bind_response.status_code == 201
    assert bind_response.json()["knowledge_document"]["title"] == "Refund policy"
    assert bindings_response.status_code == 200
    assert bindings_response.json()["total"] == 1
    assert unbind_response.status_code == 204
    assert bindings_after_unbind.status_code == 200
    assert bindings_after_unbind.json()["total"] == 0


@pytest.mark.anyio
async def test_upsert_company_profile(session, admin_auth_settings):
    app = create_app()

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token = await _login(ac)
        save_response = await ac.put(
            "/v1/company-profile",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "AMO Voice",
                "description": "Conversational sales automation.",
                "value_proposition": "Speed up lead qualification.",
                "target_audience": "SMB sales teams.",
                "contact_info": "support@example.com",
                "website_url": "https://example.com",
                "working_hours": "Mon-Fri 09:00-18:00",
                "compliance_notes": "No fake guarantees.",
                "is_active": True,
                "config": {"locale": "ru-RU"},
            },
        )
        get_response = await ac.get(
            "/v1/company-profile",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert save_response.status_code == 200
    assert save_response.json()["name"] == "AMO Voice"
    assert get_response.status_code == 200
    assert get_response.json()["config"]["locale"] == "ru-RU"

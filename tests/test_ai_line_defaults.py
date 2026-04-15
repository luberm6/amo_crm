"""
Tests for canonical AI line defaults:
  - TelephonyLine.is_recommended_for_ai property
  - is_recommended_for_ai exposed in GET /v1/telephony/mango/lines
  - suggested_telephony_remote_line_id in GET /v1/agent-profiles/{id}/settings
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.core.config as cfg
from app.db.session import get_db
from app.main import create_app
from app.models.agent_profile import AgentProfile
from app.models.telephony_line import TelephonyLine
from app.repositories.agent_profile_repo import AgentProfileRepository
from app.repositories.telephony_line_repo import TelephonyLineRepository


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def admin_auth_settings():
    with (
        patch.object(cfg.settings, "admin_email", "ai_line_admin@example.com"),
        patch.object(cfg.settings, "admin_password", "ai-line-secret"),
        patch.object(cfg.settings, "admin_auth_secret", "ai-line-signing"),
        patch.object(cfg.settings, "admin_token_ttl_seconds", 600),
    ):
        yield


async def _login(ac: AsyncClient) -> str:
    response = await ac.post(
        "/v1/admin/auth/login",
        json={"email": "ai_line_admin@example.com", "password": "ai-line-secret"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


async def _make_line(session: AsyncSession, **kwargs) -> TelephonyLine:
    defaults = dict(
        provider="mango",
        provider_resource_id="999000",
        phone_number="+79990000000",
        is_active=True,
        is_inbound_enabled=True,
        is_outbound_enabled=False,
        raw_payload={},
    )
    defaults.update(kwargs)
    line = TelephonyLine(**defaults)
    repo = TelephonyLineRepository(TelephonyLine, session)
    return await repo.save(line)


async def _make_agent(session: AsyncSession, **kwargs) -> AgentProfile:
    defaults = dict(
        name="Test Agent",
        is_active=True,
        system_prompt="Test",
        voice_provider="gemini",
        voice_strategy="gemini_primary",
    )
    defaults.update(kwargs)
    agent = AgentProfile(**defaults)
    repo = AgentProfileRepository(AgentProfile, session)
    return await repo.save(agent)


# ── model unit tests ─────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_is_recommended_for_ai_by_schema_name():
    line = TelephonyLine(
        provider="mango",
        provider_resource_id="111",
        phone_number="+79300350609",
        schema_name="ДЛЯ ИИ менеджера",
        is_active=True,
        is_inbound_enabled=True,
        is_outbound_enabled=False,
        raw_payload={},
    )
    assert line.is_recommended_for_ai is True


@pytest.mark.anyio
async def test_is_recommended_for_ai_by_canonical_id():
    line = TelephonyLine(
        provider="mango",
        provider_resource_id="405622036",
        phone_number="+79300350609",
        schema_name=None,
        is_active=True,
        is_inbound_enabled=True,
        is_outbound_enabled=False,
        raw_payload={},
    )
    assert line.is_recommended_for_ai is True


@pytest.mark.anyio
async def test_is_recommended_for_ai_false_for_regular_line():
    line = TelephonyLine(
        provider="mango",
        provider_resource_id="123456",
        phone_number="+79991234567",
        schema_name="Основной номер",
        is_active=True,
        is_inbound_enabled=True,
        is_outbound_enabled=False,
        raw_payload={},
    )
    assert line.is_recommended_for_ai is False


# ── API: GET /v1/telephony/mango/lines ───────────────────────────────────────

@pytest.mark.anyio
async def test_lines_api_returns_is_recommended_for_ai_true_by_schema_name(session, admin_auth_settings):
    await _make_line(
        session,
        provider_resource_id="lin_schema_ai",
        phone_number="+79300350609",
        schema_name="ДЛЯ ИИ менеджера",
    )

    app = create_app()
    async def override_get_db():
        yield session
    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token = await _login(ac)
        resp = await ac.get("/v1/telephony/mango/lines", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    items = resp.json()["items"]
    target = next((i for i in items if i["provider_resource_id"] == "lin_schema_ai"), None)
    assert target is not None
    assert target["is_recommended_for_ai"] is True


@pytest.mark.anyio
async def test_lines_api_returns_is_recommended_for_ai_true_by_canonical_id(session, admin_auth_settings):
    await _make_line(
        session,
        provider_resource_id="405622036",
        phone_number="+79300350609",
        schema_name=None,
    )

    app = create_app()
    async def override_get_db():
        yield session
    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token = await _login(ac)
        resp = await ac.get("/v1/telephony/mango/lines", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    items = resp.json()["items"]
    target = next((i for i in items if i["provider_resource_id"] == "405622036"), None)
    assert target is not None
    assert target["is_recommended_for_ai"] is True


@pytest.mark.anyio
async def test_lines_api_returns_is_recommended_for_ai_false_for_regular_line(session, admin_auth_settings):
    await _make_line(
        session,
        provider_resource_id="ordinary_line_99",
        phone_number="+79991112233",
        schema_name="Обычный номер",
    )

    app = create_app()
    async def override_get_db():
        yield session
    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token = await _login(ac)
        resp = await ac.get("/v1/telephony/mango/lines", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    items = resp.json()["items"]
    target = next((i for i in items if i["provider_resource_id"] == "ordinary_line_99"), None)
    assert target is not None
    assert target["is_recommended_for_ai"] is False


# ── API: GET /v1/agent-profiles/{id}/settings — suggested line ───────────────

@pytest.mark.anyio
async def test_agent_settings_returns_suggested_line_when_no_binding(session, admin_auth_settings):
    """Agent without telephony_line_id → suggested_telephony_remote_line_id = AI line id."""
    ai_line = await _make_line(
        session,
        provider_resource_id="405622036",
        phone_number="+79300350609",
        schema_name="ДЛЯ ИИ менеджера",
    )
    agent = await _make_agent(session, telephony_line_id=None)

    app = create_app()
    async def override_get_db():
        yield session
    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token = await _login(ac)
        resp = await ac.get(
            f"/v1/agent-profiles/{agent.id}/settings",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["telephony_remote_line_id"] is None
    assert body["suggested_telephony_remote_line_id"] == ai_line.provider_resource_id


@pytest.mark.anyio
async def test_agent_settings_no_suggestion_when_line_already_bound(session, admin_auth_settings):
    """Agent with telephony_line_id → suggested_telephony_remote_line_id must be None."""
    line = await _make_line(
        session,
        provider_resource_id="405622036",
        phone_number="+79300350609",
        schema_name="ДЛЯ ИИ менеджера",
    )
    agent = await _make_agent(session, telephony_line_id=line.id, telephony_provider="mango")

    app = create_app()
    async def override_get_db():
        yield session
    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token = await _login(ac)
        resp = await ac.get(
            f"/v1/agent-profiles/{agent.id}/settings",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["telephony_remote_line_id"] == line.provider_resource_id
    assert body["suggested_telephony_remote_line_id"] is None


@pytest.mark.anyio
async def test_agent_settings_no_suggestion_when_no_ai_line_synced(session, admin_auth_settings):
    """Agent without binding AND no AI line in inventory → suggested is None."""
    # Only regular line in DB
    await _make_line(session, provider_resource_id="ordinary_77", phone_number="+79991112233")
    agent = await _make_agent(session, telephony_line_id=None)

    app = create_app()
    async def override_get_db():
        yield session
    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token = await _login(ac)
        resp = await ac.get(
            f"/v1/agent-profiles/{agent.id}/settings",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["suggested_telephony_remote_line_id"] is None

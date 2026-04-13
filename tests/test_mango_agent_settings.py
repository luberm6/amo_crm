from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from unittest.mock import patch
import hashlib
import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.core.config as cfg
from app.db.session import get_db
from app.integrations.telephony.mango_client import (
    MangoApiConfig,
    MangoExtensionPayload,
    MangoLinePayload,
    _build_signed_payload,
)
from app.main import create_app
from app.models.agent_profile import AgentProfile
from app.models.knowledge_document import KnowledgeDocument
from app.models.telephony_line import TelephonyLine
from app.repositories.agent_profile_repo import AgentProfileRepository
from app.repositories.knowledge_document_repo import KnowledgeDocumentRepository
from app.services.mango_telephony_service import MangoTelephonyService
from app.services.telephony_routing import (
    resolve_agent_to_mango_line,
    resolve_inbound_number_to_agent,
)
from app.integrations.telephony.mango_client import normalize_mango_phone


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


@dataclass
class _FakeMangoClient:
    config: MangoApiConfig
    lines: list[MangoLinePayload]
    extensions: list[MangoExtensionPayload]
    closed: bool = False

    async def list_lines(self) -> list[MangoLinePayload]:
        return list(self.lines)

    async def list_extensions(self) -> list[MangoExtensionPayload]:
        return list(self.extensions)

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.anyio
async def test_mango_signature_uses_key_json_salt_order() -> None:
    payload = {"line_number": "0", "to": {"number": "+79990000000"}}
    signed = _build_signed_payload("api-key", "api-salt", payload)
    json_payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    expected_sign = hashlib.sha256(f"api-key{json_payload}api-salt".encode("utf-8")).hexdigest()

    assert signed["vpbx_api_key"] == "api-key"
    assert signed["json"] == json_payload
    assert signed["sign"] == expected_sign


@pytest.mark.anyio
async def test_mango_line_sync_maps_inventory_and_extensions(session: AsyncSession) -> None:
    client = _FakeMangoClient(
        config=MangoApiConfig(
            base_url="https://app.mango-office.ru/vpbx",
            api_key="api-key",
            api_salt="api-salt",
        ),
        lines=[
            MangoLinePayload(
                provider_resource_id="line-1",
                phone_number="+74951234567",
                display_name="Main line",
                extension=None,
                is_active=True,
                is_inbound_enabled=True,
                is_outbound_enabled=False,
                raw_payload={"id": "line-1", "number": "+74951234567"},
            )
        ],
        extensions=[
            MangoExtensionPayload(
                provider_resource_id="user-101",
                extension="101",
                display_name="Alice",
                line_provider_resource_id="line-1",
                line_phone_number="+74951234567",
                raw_payload={"id": "user-101", "extension": "101"},
            )
        ],
    )
    service = MangoTelephonyService(session, client=client)

    result = await service.sync_lines()

    assert result.synced_count == 1
    assert result.deactivated_count == 0
    assert len(result.items) == 1
    line = result.items[0]
    assert line.provider == "mango"
    assert line.provider_resource_id == "line-1"
    assert line.phone_number == "+74951234567"
    assert line.extension == "101"
    assert line.is_outbound_enabled is True
    assert line.synced_at is not None


@pytest.mark.anyio
async def test_agent_settings_api_saves_mango_binding_and_knowledge(
    session: AsyncSession,
    admin_auth_settings,
) -> None:
    app = create_app()

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db

    agent = await AgentProfileRepository(AgentProfile, session).save(
        AgentProfile(
            name="Sales Mango",
            is_active=True,
            system_prompt="Base prompt",
            voice_strategy="tts_primary",
            voice_provider="elevenlabs",
            config={"locale": "ru-RU"},
            version=1,
        )
    )
    line = await session.merge(
        TelephonyLine(
            provider="mango",
            provider_resource_id="line-42",
            phone_number="+74950000042",
            display_name="Support line",
            extension="142",
            is_active=True,
            is_inbound_enabled=True,
            is_outbound_enabled=True,
            raw_payload={"id": "line-42"},
            synced_at=datetime.now(timezone.utc),
        )
    )
    await session.flush()
    document = await KnowledgeDocumentRepository(KnowledgeDocument, session).save(
        KnowledgeDocument(
            title="Delivery FAQ",
            category="faq",
            content="We deliver in 24 hours.",
            is_active=True,
            metadata_json={},
        )
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token = await _login(ac)
        patch_response = await ac.patch(
            f"/v1/agent-profiles/{agent.id}/settings",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "voice_provider": "gemini",
                "telephony_provider": "mango",
                "telephony_line_id": str(line.id),
                "telephony_extension": "142",
                "system_prompt": "Updated prompt",
                "user_settings": {"locale": "ru-RU", "gemini_voice_name": "Aoede"},
                "knowledge_document_ids": [str(document.id)],
            },
        )
        get_response = await ac.get(
            f"/v1/agent-profiles/{agent.id}/settings",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert patch_response.status_code == 200
    payload = patch_response.json()
    assert payload["voice_provider"] == "gemini"
    assert payload["voice_strategy"] == "gemini_primary"
    assert payload["telephony_provider"] == "mango"
    assert payload["telephony_line"]["phone_number"] == "+74950000042"
    assert payload["knowledge_document_ids"] == [str(document.id)]
    assert get_response.status_code == 200
    assert get_response.json()["telephony_extension"] == "142"


@pytest.mark.anyio
async def test_agent_settings_api_rejects_inactive_telephony_line(
    session: AsyncSession,
    admin_auth_settings,
) -> None:
    app = create_app()

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db

    agent = await AgentProfileRepository(AgentProfile, session).save(
        AgentProfile(
            name="Sales Mango",
            is_active=True,
            system_prompt="Base prompt",
            voice_strategy="tts_primary",
            voice_provider="elevenlabs",
            config={},
            version=1,
        )
    )
    line = await session.merge(
        TelephonyLine(
            provider="mango",
            provider_resource_id="line-dead",
            phone_number="+74950000099",
            display_name="Inactive line",
            extension=None,
            is_active=False,
            is_inbound_enabled=True,
            is_outbound_enabled=False,
            raw_payload={"id": "line-dead"},
            synced_at=datetime.now(timezone.utc),
        )
    )
    await session.flush()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token = await _login(ac)
        response = await ac.patch(
            f"/v1/agent-profiles/{agent.id}/settings",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "telephony_provider": "mango",
                "telephony_line_id": str(line.id),
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "telephony_line_inactive"


@pytest.mark.anyio
async def test_mango_lines_endpoint_lists_cached_inventory(
    session: AsyncSession,
    admin_auth_settings,
) -> None:
    app = create_app()

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db

    await session.merge(
        TelephonyLine(
            provider="mango",
            provider_resource_id="line-cache",
            phone_number="+74957770000",
            display_name="Cached line",
            extension="700",
            is_active=True,
            is_inbound_enabled=True,
            is_outbound_enabled=True,
            raw_payload={"id": "line-cache"},
            synced_at=datetime.now(timezone.utc),
        )
    )
    await session.flush()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token = await _login(ac)
        response = await ac.get(
            "/v1/telephony/mango/lines",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] >= 1
    assert any(item["phone_number"] == "+74957770000" for item in payload["items"])


@pytest.mark.anyio
async def test_normalize_mango_phone_variants() -> None:
    assert normalize_mango_phone("79300350609") == "+79300350609"
    assert normalize_mango_phone("+79300350609") == "+79300350609"
    assert normalize_mango_phone("9300350609") == "+79300350609"
    assert normalize_mango_phone("74951234567") == "+74951234567"
    assert normalize_mango_phone("") == ""
    assert normalize_mango_phone(None) == ""
    # Non-RU — returned as-is (stripped)
    assert normalize_mango_phone("+12125551234") == "+12125551234"


@pytest.mark.anyio
async def test_mango_line_sync_stores_normalized_phone(session: AsyncSession) -> None:
    """
    MangoClient.list_lines() normalizes phones before returning MangoLinePayload.
    The sync service stores whatever the client provides.
    This test verifies that a normalized phone from the client is persisted as-is.
    """
    client = _FakeMangoClient(
        config=MangoApiConfig(
            base_url="https://app.mango-office.ru/vpbx",
            api_key="api-key",
            api_salt="api-salt",
        ),
        lines=[
            MangoLinePayload(
                provider_resource_id="line-norm2",
                phone_number=normalize_mango_phone("79585382099"),  # pre-normalized as real client does
                display_name="Test normalization",
                extension=None,
                is_active=True,
                is_inbound_enabled=True,
                is_outbound_enabled=False,
                raw_payload={"number": "79585382099"},
            )
        ],
        extensions=[],
    )
    service = MangoTelephonyService(session, client=client)
    result = await service.sync_lines()

    assert any(item.phone_number == "+79585382099" for item in result.items)


@pytest.mark.anyio
async def test_resolve_inbound_number_to_agent(session: AsyncSession) -> None:
    line = await session.merge(
        TelephonyLine(
            provider="mango",
            provider_resource_id="line-route",
            phone_number="+79300350609",
            display_name="AI Line",
            extension=None,
            is_active=True,
            is_inbound_enabled=True,
            is_outbound_enabled=False,
            raw_payload={},
            synced_at=datetime.now(timezone.utc),
        )
    )
    await session.flush()

    agent = await AgentProfileRepository(AgentProfile, session).save(
        AgentProfile(
            name="Route Agent",
            is_active=True,
            system_prompt="Prompt",
            voice_strategy="tts_primary",
            voice_provider="elevenlabs",
            config={},
            version=1,
            telephony_provider="mango",
            telephony_line_id=line.id,
        )
    )
    await session.flush()

    # Lookup with normalized form
    found = await resolve_inbound_number_to_agent(session, "79300350609")
    assert found is not None
    assert found.id == agent.id

    # Lookup with already-normalized form
    found2 = await resolve_inbound_number_to_agent(session, "+79300350609")
    assert found2 is not None
    assert found2.id == agent.id

    # Unknown number → None
    assert await resolve_inbound_number_to_agent(session, "+70000000000") is None


@pytest.mark.anyio
async def test_resolve_agent_to_mango_line(session: AsyncSession) -> None:
    line = await session.merge(
        TelephonyLine(
            provider="mango",
            provider_resource_id="line-rev",
            phone_number="+79000000001",
            display_name="Rev line",
            extension=None,
            is_active=True,
            is_inbound_enabled=True,
            is_outbound_enabled=False,
            raw_payload={},
            synced_at=datetime.now(timezone.utc),
        )
    )
    await session.flush()

    agent = await AgentProfileRepository(AgentProfile, session).save(
        AgentProfile(
            name="Rev Agent",
            is_active=True,
            system_prompt="Prompt",
            voice_strategy="tts_primary",
            voice_provider="elevenlabs",
            config={},
            version=1,
            telephony_provider="mango",
            telephony_line_id=line.id,
        )
    )
    await session.flush()

    resolved = await resolve_agent_to_mango_line(session, agent.id)
    assert resolved is not None
    assert resolved.id == line.id
    assert resolved.phone_number == "+79000000001"

    # Agent without binding → None
    unbound = await AgentProfileRepository(AgentProfile, session).save(
        AgentProfile(
            name="Unbound Agent",
            is_active=True,
            system_prompt="Prompt",
            voice_strategy="tts_primary",
            voice_provider="elevenlabs",
            config={},
            version=1,
        )
    )
    assert await resolve_agent_to_mango_line(session, unbound.id) is None

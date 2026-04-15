"""
Tests for Mango inbound webhook security, payload parsing, agent routing,
and admin observability endpoints (routing-map, debug/resolve-inbound).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

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


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_app(session: AsyncSession):
    """Create a FastAPI test app with DB overridden."""
    from app.api.deps import get_call_engine, get_transfer_engine
    from app.integrations.call_engine.stub import StubEngine
    from app.integrations.call_engine.router_engine import RoutingCallEngine
    from app.integrations.transfer_engine.stub import StubTransferEngine

    application = create_app()

    async def override_get_db():
        yield session

    async def override_get_call_engine():
        return RoutingCallEngine(
            vapi_engine=None,
            direct_engine=None,
            browser_engine=None,
            fallback_engine=StubEngine(),
        )

    def override_get_transfer_engine():
        return StubTransferEngine()

    application.dependency_overrides[get_db] = override_get_db
    application.dependency_overrides[get_call_engine] = override_get_call_engine
    application.dependency_overrides[get_transfer_engine] = override_get_transfer_engine
    return application


def _sign_payload(raw_body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


async def _make_line(session: AsyncSession, *, phone_number: str = "+79300350609") -> TelephonyLine:
    line = TelephonyLine(
        provider="mango",
        provider_resource_id=str(uuid.uuid4()),
        phone_number=phone_number,
        display_name="Test Line",
        is_active=True,
        is_inbound_enabled=True,
        is_outbound_enabled=False,
    )
    session.add(line)
    await session.flush()
    return line


async def _make_agent(
    session: AsyncSession,
    *,
    name: str = "Test Agent",
    line: TelephonyLine,
    is_active: bool = True,
) -> AgentProfile:
    agent = AgentProfile(
        name=name,
        is_active=is_active,
        system_prompt="You are a test agent.",
        voice_strategy="tts_primary",
        voice_provider="elevenlabs",
        config={},
        version=1,
        telephony_provider="mango",
        telephony_line_id=line.id,
    )
    session.add(agent)
    await session.flush()
    return agent


async def _admin_login(ac: AsyncClient) -> str:
    resp = await ac.post(
        "/v1/admin/auth/login",
        json={"email": "admin@example.com", "password": "super-secret"},
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def admin_auth_settings():
    with (
        patch.object(cfg.settings, "admin_email", "admin@example.com"),
        patch.object(cfg.settings, "admin_password", "super-secret"),
        patch.object(cfg.settings, "admin_auth_secret", "signing-secret"),
        patch.object(cfg.settings, "admin_token_ttl_seconds", 600),
    ):
        yield


# ── Webhook security tests ─────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_webhook_bad_json_returns_400(session: AsyncSession) -> None:
    app = _make_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/v1/webhooks/mango",
            content=b"not valid json",
            headers={"content-type": "application/json"},
        )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "mango_webhook_bad_payload"


@pytest.mark.anyio
async def test_webhook_missing_signature_returns_401(session: AsyncSession) -> None:
    secret = "test-webhook-secret"
    payload = json.dumps({"event": "call_start"}).encode()
    app = _make_app(session)
    with patch.object(cfg.settings, "mango_webhook_secret", secret):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/webhooks/mango",
                content=payload,
                headers={"content-type": "application/json"},
                # no x-mango-signature header
            )
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"] == "mango_webhook_not_configured"
    assert body["detail"] == "missing_signature"


@pytest.mark.anyio
async def test_webhook_invalid_signature_returns_401(session: AsyncSession) -> None:
    secret = "test-webhook-secret"
    payload = json.dumps({"event": "call_start"}).encode()
    app = _make_app(session)
    with patch.object(cfg.settings, "mango_webhook_secret", secret):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/webhooks/mango",
                content=payload,
                headers={
                    "content-type": "application/json",
                    "x-mango-signature": "deadbeef00000000000000000000000000000000000000000000000000000000",
                },
            )
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"] == "mango_webhook_invalid_signature"
    assert body["detail"] == "invalid_signature"


@pytest.mark.anyio
async def test_webhook_valid_signature_returns_200(session: AsyncSession) -> None:
    secret = "test-webhook-secret"
    payload_dict = {"event": "call_start", "entry": {"id": "leg-abc"}}
    payload = json.dumps(payload_dict).encode()
    sig = _sign_payload(payload, secret)
    app = _make_app(session)
    with patch.object(cfg.settings, "mango_webhook_secret", secret):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/webhooks/mango",
                content=payload,
                headers={
                    "content-type": "application/json",
                    "x-mango-signature": sig,
                },
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


@pytest.mark.anyio
async def test_webhook_no_secret_accepts_request(session: AsyncSession) -> None:
    """When no webhook secret is configured, requests pass through (with warning)."""
    payload = json.dumps({"event": "call_start"}).encode()
    app = _make_app(session)
    with (
        patch.object(cfg.settings, "mango_webhook_secret", ""),
        patch.object(cfg.settings, "mango_webhook_shared_secret", ""),
        patch.object(cfg.settings, "mango_webhook_ip_allowlist", ""),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/webhooks/mango",
                content=payload,
                headers={"content-type": "application/json"},
            )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.anyio
async def test_webhook_sha256_prefix_accepted(session: AsyncSession) -> None:
    """Signature with 'sha256=' prefix is also accepted."""
    secret = "test-webhook-secret"
    payload = json.dumps({"event": "call_start"}).encode()
    sig = "sha256=" + _sign_payload(payload, secret)
    app = _make_app(session)
    with patch.object(cfg.settings, "mango_webhook_secret", secret):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/webhooks/mango",
                content=payload,
                headers={
                    "content-type": "application/json",
                    "x-mango-signature": sig,
                },
            )
    assert resp.status_code == 200


# ── Inbound routing via webhook ────────────────────────────────────────────────

@pytest.mark.anyio
async def test_webhook_routes_inbound_to_agent(session: AsyncSession) -> None:
    """Inbound number matching a line with a bound agent → agent resolved."""
    line = await _make_line(session, phone_number="+79300350609")
    agent = await _make_agent(session, line=line)
    await session.flush()

    payload_dict = {
        "event": "call_start",
        "entry": {"id": "leg-001", "to": {"number": "79300350609"}},
    }
    payload = json.dumps(payload_dict).encode()
    app = _make_app(session)

    with (
        patch.object(cfg.settings, "mango_webhook_secret", ""),
        patch.object(cfg.settings, "mango_webhook_shared_secret", ""),
        patch.object(cfg.settings, "mango_webhook_ip_allowlist", ""),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/webhooks/mango",
                content=payload,
                headers={"content-type": "application/json"},
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["webhook_secured"] is False
    assert body["routing"]["phone_number_input"] == "79300350609"
    assert body["routing"]["phone_number_normalized"] == "+79300350609"
    assert body["routing"]["line_found"] is True
    assert body["routing"]["agent_found"] is True
    assert body["routing"]["agent_id"] == str(agent.id)
    assert body["inbound_launch"]["status"] == "blocked"
    assert "media_gateway_disabled" in body["inbound_launch"]["reason"]


@pytest.mark.anyio
async def test_webhook_inbound_no_matching_line(session: AsyncSession) -> None:
    """Inbound number with no matching line → still 200, just no agent found."""
    payload_dict = {
        "event": "call_start",
        "entry": {"id": "leg-002", "to": {"number": "79999999999"}},
    }
    payload = json.dumps(payload_dict).encode()
    app = _make_app(session)

    with (
        patch.object(cfg.settings, "mango_webhook_secret", ""),
        patch.object(cfg.settings, "mango_webhook_shared_secret", ""),
        patch.object(cfg.settings, "mango_webhook_ip_allowlist", ""),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/webhooks/mango",
                content=payload,
                headers={"content-type": "application/json"},
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["routing"]["line_found"] is False
    assert body["routing"]["agent_found"] is False
    assert body["routing"]["phone_number_normalized"] == "+79999999999"


# ── Routing map endpoint ───────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_routing_map_returns_all_lines(
    session: AsyncSession,
    admin_auth_settings,
) -> None:
    """GET /v1/telephony/mango/routing-map returns all Mango lines."""
    line1 = await _make_line(session, phone_number="+74951111111")
    line2 = await _make_line(session, phone_number="+74952222222")
    agent = await _make_agent(session, name="Agent Alpha", line=line1)
    await session.flush()

    app = _make_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token = await _admin_login(ac)
        resp = await ac.get(
            "/v1/telephony/mango/routing-map",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 2

    line1_item = next((i for i in body["items"] if i["phone_number"] == "+74951111111"), None)
    line2_item = next((i for i in body["items"] if i["phone_number"] == "+74952222222"), None)

    assert line1_item is not None
    assert line1_item["agent_name"] == "Agent Alpha"
    assert line1_item["agent_id"] == str(agent.id)
    assert line1_item["agent_is_active"] is True

    assert line2_item is not None
    assert line2_item["agent_id"] is None
    assert line2_item["agent_name"] is None


@pytest.mark.anyio
async def test_routing_map_requires_auth(session: AsyncSession, admin_auth_settings) -> None:
    app = _make_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/v1/telephony/mango/routing-map")
    assert resp.status_code in (401, 403)


# ── Debug resolve-inbound endpoint ─────────────────────────────────────────────

@pytest.mark.anyio
async def test_debug_resolve_inbound_finds_agent(
    session: AsyncSession,
    admin_auth_settings,
) -> None:
    """POST /v1/telephony/mango/debug/resolve-inbound resolves number to agent."""
    line = await _make_line(session, phone_number="+79300350609")
    agent = await _make_agent(session, name="Inbound Agent", line=line)
    await session.flush()

    app = _make_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token = await _admin_login(ac)
        resp = await ac.post(
            "/v1/telephony/mango/debug/resolve-inbound",
            json={"phone_number": "79300350609"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["phone_number_input"] == "79300350609"
    assert body["phone_number_normalized"] == "+79300350609"
    assert body["line_found"] is True
    assert body["line_id"] == str(line.id)
    assert body["agent_found"] is True
    assert body["agent_id"] == str(agent.id)
    assert body["agent_name"] == "Inbound Agent"
    assert body["ambiguous"] is False
    assert body["candidate_count"] == 1


@pytest.mark.anyio
async def test_debug_resolve_inbound_line_not_found(
    session: AsyncSession,
    admin_auth_settings,
) -> None:
    app = _make_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token = await _admin_login(ac)
        resp = await ac.post(
            "/v1/telephony/mango/debug/resolve-inbound",
            json={"phone_number": "79888000000"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["line_found"] is False
    assert body["agent_found"] is False
    assert body["phone_number_normalized"] == "+79888000000"


@pytest.mark.anyio
async def test_debug_resolve_inbound_line_found_no_agent(
    session: AsyncSession,
    admin_auth_settings,
) -> None:
    line = await _make_line(session, phone_number="+79300111222")
    await session.flush()

    app = _make_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token = await _admin_login(ac)
        resp = await ac.post(
            "/v1/telephony/mango/debug/resolve-inbound",
            json={"phone_number": "79300111222"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["line_found"] is True
    assert body["line_id"] == str(line.id)
    assert body["agent_found"] is False
    assert body["agent_id"] is None
    assert body["candidate_count"] == 0


@pytest.mark.anyio
async def test_debug_resolve_inbound_ambiguous(
    session: AsyncSession,
    admin_auth_settings,
) -> None:
    """Two active agents on the same line → ambiguous=True, first agent returned."""
    line = await _make_line(session, phone_number="+79300555666")
    agent1 = await _make_agent(session, name="Agent One", line=line)
    agent2 = await _make_agent(session, name="Agent Two", line=line)
    await session.flush()

    app = _make_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token = await _admin_login(ac)
        resp = await ac.post(
            "/v1/telephony/mango/debug/resolve-inbound",
            json={"phone_number": "79300555666"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["line_found"] is True
    assert body["agent_found"] is True
    assert body["ambiguous"] is True
    assert body["candidate_count"] == 2
    # First agent (by created_at asc) is returned
    assert body["agent_id"] == str(agent1.id)


@pytest.mark.anyio
async def test_debug_resolve_inbound_requires_auth(session: AsyncSession, admin_auth_settings) -> None:
    app = _make_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/v1/telephony/mango/debug/resolve-inbound",
            json={"phone_number": "79300350609"},
        )
    assert resp.status_code in (401, 403)


@pytest.mark.anyio
async def test_debug_resolve_outbound_returns_agent_bound_line(
    session: AsyncSession,
    admin_auth_settings,
) -> None:
    line = await _make_line(session, phone_number="+79300350609")
    line.provider_resource_id = "405622036"
    line.schema_name = "ДЛЯ ИИ менеджера"
    line.display_name = "ДЛЯ ИИ менеджера"
    agent = await _make_agent(session, name="Outbound Agent", line=line)
    await session.flush()

    app = _make_app(session)
    with patch.object(cfg.settings, "mango_from_ext", "101"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            token = await _admin_login(ac)
            resp = await ac.get(
                f"/v1/telephony/mango/debug/resolve-outbound/{agent.id}",
                headers={"Authorization": f"Bearer {token}"},
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["agent_found"] is True
    assert body["agent_name"] == "Outbound Agent"
    assert body["line_found"] is True
    assert body["remote_line_id"] == "405622036"
    assert body["line_phone_number"] == "+79300350609"
    assert body["line_schema_name"] == "ДЛЯ ИИ менеджера"
    assert body["from_ext_configured"] is True
    assert body["originate_ready"] is True
    assert body["missing_requirements"] == []


@pytest.mark.anyio
async def test_debug_resolve_outbound_reports_missing_from_ext(
    session: AsyncSession,
    admin_auth_settings,
) -> None:
    line = await _make_line(session, phone_number="+79300350609")
    line.provider_resource_id = "405622036"
    line.schema_name = "ДЛЯ ИИ менеджера"
    agent = await _make_agent(session, name="Outbound Agent", line=line)
    await session.flush()

    app = _make_app(session)
    with (
        patch.object(cfg.settings, "mango_from_ext", ""),
        patch(
            "app.api.v1.telephony.resolve_mango_from_ext",
            AsyncMock(
                return_value=type(
                    "_Resolved",
                    (),
                    {
                        "value": "10",
                        "source": "auto_discovered_first_extension",
                    },
                )()
            ),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            token = await _admin_login(ac)
            resp = await ac.get(
                f"/v1/telephony/mango/debug/resolve-outbound/{agent.id}",
                headers={"Authorization": f"Bearer {token}"},
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["agent_found"] is True
    assert body["line_found"] is True
    assert body["from_ext_configured"] is False
    assert body["originate_ready"] is True
    assert body["resolved_from_ext"] in {"10", "12"}
    assert body["from_ext_source"] in {"auto_discovered_by_line", "auto_discovered_first_extension"}


# ── Additional edge-case tests ─────────────────────────────────────────────────

@pytest.mark.anyio
async def test_webhook_no_to_number_has_no_routing(session: AsyncSession) -> None:
    """Webhook payload without a to-number produces no routing or inbound_launch."""
    payload = json.dumps({"event": "call_start", "entry": {"id": "leg-x"}}).encode()
    app = _make_app(session)
    with (
        patch.object(cfg.settings, "mango_webhook_secret", ""),
        patch.object(cfg.settings, "mango_webhook_shared_secret", ""),
        patch.object(cfg.settings, "mango_webhook_ip_allowlist", ""),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/webhooks/mango",
                content=payload,
                headers={"content-type": "application/json"},
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["routing"] is None
    assert body["inbound_launch"] is None


@pytest.mark.anyio
async def test_debug_resolve_outbound_agent_not_found(
    session: AsyncSession,
    admin_auth_settings,
) -> None:
    """Unknown agent UUID → agent_found=False, originate_ready=False."""
    random_id = uuid.uuid4()
    app = _make_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token = await _admin_login(ac)
        resp = await ac.get(
            f"/v1/telephony/mango/debug/resolve-outbound/{random_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent_found"] is False
    assert body["originate_ready"] is False
    assert "agent_not_found_or_inactive" in body["missing_requirements"]


@pytest.mark.anyio
async def test_debug_resolve_outbound_agent_has_no_line(
    session: AsyncSession,
    admin_auth_settings,
) -> None:
    """Agent with no telephony_line → line_found=False, originate_ready=False."""
    agent = AgentProfile(
        name="No-line Agent",
        is_active=True,
        system_prompt="test",
        voice_strategy="tts_primary",
        voice_provider="elevenlabs",
        config={},
        version=1,
    )
    session.add(agent)
    await session.flush()

    app = _make_app(session)
    with patch.object(cfg.settings, "mango_from_ext", "101"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            token = await _admin_login(ac)
            resp = await ac.get(
                f"/v1/telephony/mango/debug/resolve-outbound/{agent.id}",
                headers={"Authorization": f"Bearer {token}"},
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent_found"] is True
    assert body["line_found"] is False
    assert body["originate_ready"] is False
    assert "agent_has_no_mango_line" in body["missing_requirements"]


@pytest.mark.anyio
async def test_mango_readiness_reports_local_backend_as_blocker(
    session: AsyncSession,
    admin_auth_settings,
) -> None:
    app = _make_app(session)
    with (
        patch.object(cfg.settings, "mango_api_key", "api-key"),
        patch.object(cfg.settings, "mango_api_salt", "api-salt"),
        patch.object(cfg.settings, "backend_url", "http://127.0.0.1:8000"),
        patch.object(cfg.settings, "mango_webhook_secret", ""),
        patch.object(cfg.settings, "mango_webhook_shared_secret", ""),
        patch.object(cfg.settings, "mango_from_ext", ""),
        patch("app.api.v1.telephony.resolve_mango_from_ext", AsyncMock(return_value=type("_Resolved", (), {"value": "10"})())),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            token = await _admin_login(ac)
            resp = await ac.get(
                "/v1/telephony/mango/readiness",
                headers={"Authorization": f"Bearer {token}"},
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["backend_url"] == "http://127.0.0.1:8000"
    assert body["webhook_url_public"] is False
    assert body["inbound_webhook_smoke_ready"] is False
    assert "backend_url_not_public" in body["missing_requirements"]
    assert body["route_readiness"]["inbound_webhook"]["ready"] is False
    assert "BACKEND_URL is not public." in body["route_readiness"]["inbound_webhook"]["blockers"]
    assert body["render_summary"]["overall_status"] == "blocked"


@pytest.mark.anyio
async def test_mango_readiness_reports_direct_override_to_mango(
    session: AsyncSession,
    admin_auth_settings,
) -> None:
    app = _make_app(session)
    with (
        patch.object(cfg.settings, "mango_api_key", "api-key"),
        patch.object(cfg.settings, "mango_api_salt", "api-salt"),
        patch.object(cfg.settings, "gemini_api_key", "gemini-key"),
        patch.object(cfg.settings, "environment", "development"),
        patch.object(cfg.settings, "telephony_provider", "stub"),
        patch.object(cfg.settings, "backend_url", "https://voice.example.com"),
        patch.object(cfg.settings, "mango_webhook_secret", "whsec"),
        patch.object(cfg.settings, "mango_from_ext", ""),
        patch.object(cfg.settings, "media_gateway_enabled", True),
        patch.object(cfg.settings, "media_gateway_provider", "freeswitch"),
        patch.object(cfg.settings, "media_gateway_mode", "esl_rtp"),
        patch("app.api.v1.telephony.resolve_mango_from_ext", AsyncMock(return_value=type("_Resolved", (), {"value": "10"})())),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            token = await _admin_login(ac)
            resp = await ac.get(
                "/v1/telephony/mango/readiness",
                headers={"Authorization": f"Bearer {token}"},
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["telephony_runtime_provider"] == "mango"
    assert body["telephony_runtime_real"] is True
    assert body["outbound_originate_smoke_ready"] is True
    assert body["inbound_webhook_smoke_ready"] is True
    assert body["inbound_ai_runtime_ready"] is True
    assert body["route_readiness"]["outbound_originate"]["ready"] is True
    assert body["route_readiness"]["inbound_ai_runtime"]["status"] == "ready"
    assert body["render_summary"]["overall_status"] == "ready"


@pytest.mark.anyio
async def test_mango_readiness_uses_render_external_url_when_backend_url_is_local(
    session: AsyncSession,
    admin_auth_settings,
) -> None:
    app = _make_app(session)
    with (
        patch.object(cfg.settings, "mango_api_key", "api-key"),
        patch.object(cfg.settings, "mango_api_salt", "api-salt"),
        patch.object(cfg.settings, "backend_url", "http://127.0.0.1:8000"),
        patch.object(cfg.settings, "render_external_url", "https://amo-crm-api.onrender.com"),
        patch.object(cfg.settings, "mango_webhook_secret", "whsec"),
        patch.object(cfg.settings, "mango_from_ext", "101"),
        patch.object(cfg.settings, "media_gateway_enabled", False),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            token = await _admin_login(ac)
            resp = await ac.get(
                "/v1/telephony/mango/readiness",
                headers={"Authorization": f"Bearer {token}"},
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["backend_url"] == "https://amo-crm-api.onrender.com"
    assert body["webhook_url"] == "https://amo-crm-api.onrender.com/v1/webhooks/mango"
    assert body["webhook_url_public"] is True
    assert "backend_url_not_public" not in body["missing_requirements"]
    assert body["route_readiness"]["inbound_webhook"]["ready"] is True

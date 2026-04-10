"""
Тесты для CallRoutePolicy — явная политика маршрутизации.

11 тестов:
- select_route: AUTO→vapi (vapi+direct available)
- select_route: AUTO→direct (only direct available)
- select_route: AUTO→stub (nothing available, dev mode)
- select_route: VAPI mode → vapi regardless of direct
- select_route: DIRECT mode → direct regardless of vapi
- select_route: VAPI mode without vapi configured → EngineError (not silent stub)
- select_route: DIRECT mode without direct configured → EngineError (not silent stub)
- allows_fallback: AUTO vapi→direct allowed
- allows_fallback: VAPI mode vapi→direct NOT allowed (direct available but wrong mode)
- resolve_for_existing_call: uses call.route_used not mode
- select_route: AUTO→stub in production mode → EngineError (no stub fallback in prod)
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from app.core.config import settings
from app.core.exceptions import EngineError
from app.integrations.call_engine.route_policy import CallRoutePolicy
from app.models.call import Call, CallMode, CallStatus


def _make_call(mode: CallMode, route_used: str = None, vapi_call_id: str = None, mango_call_id: str = None) -> Call:
    c = MagicMock(spec=Call)
    c.id = uuid.uuid4()
    c.mode = mode
    c.route_used = route_used
    c.vapi_call_id = vapi_call_id
    c.mango_call_id = mango_call_id
    c.status = CallStatus.CREATED
    return c


# ── select_route ──────────────────────────────────────────────────────────────

def test_select_route_auto_prefers_vapi():
    """AUTO с vapi+direct → vapi."""
    policy = CallRoutePolicy(vapi_available=True, direct_available=True)
    call = _make_call(CallMode.AUTO)
    assert policy.select_route(call) == "vapi"


def test_select_route_auto_falls_to_direct_when_no_vapi():
    """AUTO без vapi, с direct → direct."""
    policy = CallRoutePolicy(vapi_available=False, direct_available=True)
    call = _make_call(CallMode.AUTO)
    assert policy.select_route(call) == "direct"


def test_select_route_auto_falls_to_stub_when_nothing():
    """AUTO без vapi и direct → stub."""
    policy = CallRoutePolicy(vapi_available=False, direct_available=False)
    call = _make_call(CallMode.AUTO)
    assert policy.select_route(call) == "stub"


def test_select_route_vapi_mode_ignores_direct():
    """VAPI mode → vapi, даже если direct тоже доступен."""
    policy = CallRoutePolicy(vapi_available=True, direct_available=True)
    call = _make_call(CallMode.VAPI)
    assert policy.select_route(call) == "vapi"


def test_select_route_direct_mode_ignores_vapi():
    """DIRECT mode → direct, даже если vapi тоже доступен."""
    policy = CallRoutePolicy(vapi_available=True, direct_available=True)
    call = _make_call(CallMode.DIRECT)
    assert policy.select_route(call) == "direct"


def test_select_route_vapi_mode_raises_when_not_configured():
    """VAPI mode без VAPI credentials → EngineError (не тихий stub)."""
    policy = CallRoutePolicy(vapi_available=False, direct_available=True)
    call = _make_call(CallMode.VAPI)
    with pytest.raises(EngineError) as exc_info:
        policy.select_route(call)
    assert "VAPI mode requested but Vapi is not configured" in str(exc_info.value)
    assert exc_info.value.error_code == "engine_error"


def test_select_route_direct_mode_raises_when_not_configured():
    """DIRECT mode без GEMINI_API_KEY → EngineError (не тихий stub)."""
    policy = CallRoutePolicy(vapi_available=True, direct_available=False)
    call = _make_call(CallMode.DIRECT)
    with pytest.raises(EngineError) as exc_info:
        policy.select_route(call)
    assert "DIRECT mode requested but Gemini is not configured" in str(exc_info.value)
    assert exc_info.value.error_code == "engine_error"


# ── allows_fallback ───────────────────────────────────────────────────────────

def test_allows_fallback_vapi_to_direct_in_auto_mode():
    """AUTO mode: vapi→direct fallback allowed."""
    policy = CallRoutePolicy(vapi_available=True, direct_available=True)
    call = _make_call(CallMode.AUTO)
    result = policy.allows_fallback(call, from_route="vapi")
    assert result == "direct"


def test_allows_fallback_vapi_to_direct_not_allowed_in_vapi_mode():
    """VAPI mode: no fallback to direct (explicit mode = explicit intent)."""
    policy = CallRoutePolicy(
        vapi_available=True,
        direct_available=True,
        allow_vapi_to_direct_fallback=False,
    )
    call = _make_call(CallMode.VAPI)
    result = policy.allows_fallback(call, from_route="vapi")
    assert result is None


# ── resolve_for_existing_call ─────────────────────────────────────────────────

def test_resolve_for_existing_call_uses_route_used():
    """resolve_for_existing_call: call.route_used берёт приоритет над mode."""
    # Вызов был создан через vapi (route_used="vapi"),
    # но текущий режим — DIRECT. Должны вернуть "vapi".
    policy = CallRoutePolicy(vapi_available=True, direct_available=True)
    call = _make_call(CallMode.DIRECT, route_used="vapi")
    assert policy.resolve_for_existing_call(call) == "vapi"


# ── production guard ──────────────────────────────────────────────────────────

def test_stub_route_raises_in_production_auto_mode(monkeypatch):
    """AUTO mode в production без реального engine → EngineError (не тихий stub fallback)."""
    monkeypatch.setattr(settings, "environment", "production")

    policy = CallRoutePolicy(vapi_available=False, direct_available=False)
    call = _make_call(CallMode.AUTO)

    with pytest.raises(EngineError, match="Production mode"):
        policy.select_route(call)

"""
Расширенные тесты RoutingCallEngine — fallback, stable routing, observability.

6 тестов:
- initiate_call: vapi fails → direct fallback (AUTO mode)
- initiate_call: vapi fails → no fallback in VAPI mode (reraises)
- stop_call: call.route_used="vapi" → calls vapi engine
- stop_call: call.route_used="direct" → calls direct engine
- initiate_call: fallback metadata recorded in result
- get_status: call without route_used → falls back to mode-based resolve
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import EngineError
from app.integrations.call_engine.base import EngineCallResult
from app.integrations.call_engine.router_engine import RoutingCallEngine
from app.models.call import Call, CallMode, CallStatus


def _make_call(
    mode: CallMode = CallMode.AUTO,
    route_used: str = None,
    vapi_call_id: str = None,
    mango_call_id: str = None,
) -> Call:
    c = MagicMock(spec=Call)
    c.id = uuid.uuid4()
    c.mode = mode
    c.route_used = route_used
    c.vapi_call_id = vapi_call_id
    c.mango_call_id = mango_call_id
    c.status = CallStatus.IN_PROGRESS
    return c


def _mock_engine(name: str = "engine", route_used: str = None) -> AsyncMock:
    engine = AsyncMock()
    engine.__class__.__name__ = name
    result = EngineCallResult(
        external_id=f"{name}-ext-id",
        initial_status=CallStatus.IN_PROGRESS,
        route_used=route_used or name,
    )
    engine.initiate_call.return_value = result
    engine.stop_call.return_value = None
    engine.send_instruction.return_value = None
    engine.get_status.return_value = CallStatus.IN_PROGRESS
    return engine


# ── Fallback tests ────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_vapi_failure_triggers_direct_fallback_in_auto_mode():
    """AUTO: если vapi.initiate_call бросает EngineError → вызывается direct."""
    vapi = _mock_engine("vapi", route_used="vapi")
    vapi.initiate_call.side_effect = EngineError("vapi timeout")
    direct = _mock_engine("direct", route_used="direct")
    fallback = _mock_engine("stub", route_used="stub")

    router = RoutingCallEngine(vapi_engine=vapi, direct_engine=direct, fallback_engine=fallback)
    call = _make_call(CallMode.AUTO)

    result = await router.initiate_call(call)

    vapi.initiate_call.assert_called_once_with(call)
    direct.initiate_call.assert_called_once_with(call)
    assert result.route_used == "direct"


@pytest.mark.anyio
async def test_vapi_failure_no_fallback_in_vapi_mode():
    """VAPI mode: EngineError от vapi пробрасывается, fallback не происходит."""
    vapi = _mock_engine("vapi")
    vapi.initiate_call.side_effect = EngineError("vapi down")
    direct = _mock_engine("direct")
    fallback = _mock_engine("stub")

    # allow_vapi_to_direct_fallback=False — явный VAPI mode не даёт fallback
    from app.integrations.call_engine.route_policy import CallRoutePolicy
    policy = CallRoutePolicy(vapi_available=True, direct_available=True, allow_vapi_to_direct_fallback=False)
    router = RoutingCallEngine(vapi_engine=vapi, direct_engine=direct, fallback_engine=fallback, policy=policy)
    call = _make_call(CallMode.VAPI)

    with pytest.raises(EngineError):
        await router.initiate_call(call)

    direct.initiate_call.assert_not_called()


# ── Stable routing tests ──────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_stop_call_uses_vapi_engine_when_route_used_vapi():
    """stop_call с route_used='vapi' → вызывается vapi, не direct."""
    vapi = _mock_engine("vapi")
    direct = _mock_engine("direct")
    fallback = _mock_engine("stub")

    router = RoutingCallEngine(vapi_engine=vapi, direct_engine=direct, fallback_engine=fallback)
    call = _make_call(CallMode.AUTO, route_used="vapi")

    await router.stop_call(call)

    vapi.stop_call.assert_called_once_with(call)
    direct.stop_call.assert_not_called()


@pytest.mark.anyio
async def test_stop_call_uses_direct_engine_when_route_used_direct():
    """stop_call с route_used='direct' → вызывается direct, не vapi."""
    vapi = _mock_engine("vapi")
    direct = _mock_engine("direct")
    fallback = _mock_engine("stub")

    router = RoutingCallEngine(vapi_engine=vapi, direct_engine=direct, fallback_engine=fallback)
    call = _make_call(CallMode.AUTO, route_used="direct")

    await router.stop_call(call)

    direct.stop_call.assert_called_once_with(call)
    vapi.stop_call.assert_not_called()


# ── Metadata / observability ──────────────────────────────────────────────────

@pytest.mark.anyio
async def test_fallback_metadata_recorded_in_result():
    """Fallback event записывается в result.metadata["fallback"]."""
    vapi = _mock_engine("vapi")
    vapi.initiate_call.side_effect = EngineError("provider error")
    direct = _mock_engine("direct", route_used="direct")

    router = RoutingCallEngine(vapi_engine=vapi, direct_engine=direct, fallback_engine=_mock_engine("stub"))
    call = _make_call(CallMode.AUTO)

    result = await router.initiate_call(call)

    assert result.metadata is not None
    fallback_meta = result.metadata.get("fallback")
    assert fallback_meta is not None
    assert fallback_meta["from_route"] == "vapi"
    assert "provider error" in fallback_meta["reason"]


@pytest.mark.anyio
async def test_get_status_without_route_used_falls_back_to_mode():
    """get_status без route_used: resolve через mode (AUTO → vapi если есть)."""
    vapi = _mock_engine("vapi")
    direct = _mock_engine("direct")
    fallback = _mock_engine("stub")

    router = RoutingCallEngine(vapi_engine=vapi, direct_engine=direct, fallback_engine=fallback)
    # route_used=None, mode=AUTO → policy infers "vapi" from vapi_call_id presence
    call = _make_call(CallMode.AUTO, route_used=None, vapi_call_id="vapi-123")

    await router.get_status(call)

    vapi.get_status.assert_called_once_with(call)
    direct.get_status.assert_not_called()

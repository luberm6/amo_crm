"""
Тесты для RoutingCallEngine — dispatcher по call.mode.

6 тестов:
- AUTO + vapi → vapi_engine
- AUTO + только direct → direct_engine
- AUTO + ничего → fallback
- VAPI → vapi_engine (игнорирует direct)
- DIRECT → direct_engine (игнорирует vapi)
- DIRECT + нет direct_engine → fallback
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.integrations.call_engine.base import EngineCallResult
from app.integrations.call_engine.router_engine import RoutingCallEngine
from app.models.call import Call, CallMode, CallStatus


def _make_call(mode: CallMode) -> Call:
    c = MagicMock(spec=Call)
    c.id = uuid.uuid4()
    c.mode = mode
    c.mango_call_id = None
    c.status = CallStatus.CREATED
    return c


def _mock_engine(name: str = "engine") -> AsyncMock:
    engine = AsyncMock()
    engine.__class__.__name__ = name
    result = EngineCallResult(
        external_id=f"{name}-ext-id",
        initial_status=CallStatus.IN_PROGRESS,
    )
    engine.initiate_call.return_value = result
    engine.stop_call.return_value = None
    engine.send_instruction.return_value = None
    engine.get_status.return_value = CallStatus.IN_PROGRESS
    return engine


# ── Тесты ─────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_auto_prefers_vapi():
    """AUTO mode выбирает vapi когда оба доступны."""
    vapi = _mock_engine("vapi")
    direct = _mock_engine("direct")
    fallback = _mock_engine("stub")
    router = RoutingCallEngine(vapi_engine=vapi, direct_engine=direct, fallback_engine=fallback)

    call = _make_call(CallMode.AUTO)
    await router.initiate_call(call)

    vapi.initiate_call.assert_called_once_with(call)
    direct.initiate_call.assert_not_called()
    fallback.initiate_call.assert_not_called()


@pytest.mark.anyio
async def test_auto_falls_to_direct_when_no_vapi():
    """AUTO без vapi → direct_engine."""
    direct = _mock_engine("direct")
    fallback = _mock_engine("stub")
    router = RoutingCallEngine(vapi_engine=None, direct_engine=direct, fallback_engine=fallback)

    call = _make_call(CallMode.AUTO)
    await router.initiate_call(call)

    direct.initiate_call.assert_called_once_with(call)
    fallback.initiate_call.assert_not_called()


@pytest.mark.anyio
async def test_auto_falls_to_stub_when_nothing_configured():
    """AUTO без vapi и direct → fallback (Stub)."""
    fallback = _mock_engine("stub")
    router = RoutingCallEngine(vapi_engine=None, direct_engine=None, fallback_engine=fallback)

    call = _make_call(CallMode.AUTO)
    await router.initiate_call(call)

    fallback.initiate_call.assert_called_once_with(call)


@pytest.mark.anyio
async def test_vapi_mode_selects_vapi_engine():
    """VAPI mode всегда выбирает vapi_engine даже если direct доступен."""
    vapi = _mock_engine("vapi")
    direct = _mock_engine("direct")
    fallback = _mock_engine("stub")
    router = RoutingCallEngine(vapi_engine=vapi, direct_engine=direct, fallback_engine=fallback)

    call = _make_call(CallMode.VAPI)
    await router.initiate_call(call)

    vapi.initiate_call.assert_called_once_with(call)
    direct.initiate_call.assert_not_called()


@pytest.mark.anyio
async def test_direct_mode_selects_direct_engine():
    """DIRECT mode всегда выбирает direct_engine даже если vapi доступен."""
    vapi = _mock_engine("vapi")
    direct = _mock_engine("direct")
    fallback = _mock_engine("stub")
    router = RoutingCallEngine(vapi_engine=vapi, direct_engine=direct, fallback_engine=fallback)

    call = _make_call(CallMode.DIRECT)
    await router.initiate_call(call)

    direct.initiate_call.assert_called_once_with(call)
    vapi.initiate_call.assert_not_called()


@pytest.mark.anyio
async def test_direct_mode_raises_when_no_direct():
    """DIRECT mode без direct_engine → EngineError (не тихий fallback)."""
    from app.core.exceptions import EngineError
    vapi = _mock_engine("vapi")
    fallback = _mock_engine("stub")
    router = RoutingCallEngine(vapi_engine=vapi, direct_engine=None, fallback_engine=fallback)

    call = _make_call(CallMode.DIRECT)
    with pytest.raises(EngineError):
        await router.initiate_call(call)

    fallback.initiate_call.assert_not_called()
    vapi.initiate_call.assert_not_called()

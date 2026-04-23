import uuid

from app.core.config import settings
from app.api.v1.browser_calls import _build_edge_proxy_ws_url
from app.integrations.telephony.base import TelephonyLegState
from app.integrations.telephony.mango_freeswitch_correlation import InMemoryMangoFreeSwitchCorrelationStore
from app.integrations.telephony.mango import MangoTelephonyAdapter


class _DummyStateStore:
    async def wait_for_leg_state(self, **kwargs):
        return await kwargs["poll_fallback"]()

    async def set_leg_state(self, *args, **kwargs):
        return None


def test_build_edge_proxy_ws_url_uses_target_host():
    settings.edge_proxy_target_url = "http://84.247.184.72"
    call_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    url = _build_edge_proxy_ws_url(call_id, "browser-token")
    assert url == "ws://84.247.184.72/v1/browser-calls/11111111-1111-1111-1111-111111111111/ws?token=browser-token"


def test_wait_for_answer_prefers_terminal_over_answer_seen_when_call_already_ended():
    corr = InMemoryMangoFreeSwitchCorrelationStore()
    adapter = MangoTelephonyAdapter.__new__(MangoTelephonyAdapter)
    adapter._corr = corr
    adapter._state = _DummyStateStore()

    import asyncio

    async def scenario():
        await corr.set_freeswitch_state(
            mango_leg_id="direct-test",
            state=TelephonyLegState.ANSWERED,
            freeswitch_uuid="direct-test",
        )
        await corr.set_freeswitch_state(
            mango_leg_id="direct-test",
            state=TelephonyLegState.TERMINATED,
            freeswitch_uuid="direct-test",
        )
        state = await adapter._wait_for_leg_state_via_correlation("direct-test")
        return state

    state = asyncio.run(scenario())
    assert state == TelephonyLegState.TERMINATED

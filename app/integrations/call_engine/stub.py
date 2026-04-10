"""
StubEngine — a no-op call engine for development and testing.
Logs all calls, returns sensible mock data, performs no real telephony.
Replace with VapiEngine or DirectEngine in production by swapping the
engine in app/api/deps.py.
"""
from app.core.logging import get_logger
from app.integrations.call_engine.base import AbstractCallEngine, EngineCallResult
from app.models.call import Call, CallStatus
log = get_logger(__name__)
class StubEngine(AbstractCallEngine):
    """
    No-op engine. Safe for local development without any external credentials.
    All actions are logged so you can trace the flow end-to-end.
    """
    async def initiate_call(self, call: Call) -> EngineCallResult:
        log.info(
            "stub_engine.initiate_call",
            call_id=str(call.id),
            phone=call.phone,
            mode=call.mode,
        )
        # Simulate Vapi assigning an external ID
        stub_external_id = f"stub-{call.id}"
        return EngineCallResult(
            external_id=stub_external_id,
            initial_status=CallStatus.QUEUED,
            route_used="stub",
            metadata={"engine": "stub"},
        )
    async def stop_call(self, call: Call) -> None:
        log.info("stub_engine.stop_call", call_id=str(call.id))
    async def send_instruction(self, call: Call, instruction: str) -> None:
        log.info(
            "stub_engine.send_instruction",
            call_id=str(call.id),
            instruction=instruction,
        )
    async def get_status(self, call: Call) -> CallStatus:
        log.info("stub_engine.get_status", call_id=str(call.id))
        # Stub always reports the same status the DB holds
        return call.status
from __future__ import annotations

from app.core.logging import get_logger
from app.integrations.call_engine.base import EngineCallResult
from app.integrations.direct.engine import DirectGeminiEngine
from app.models.call import Call

log = get_logger(__name__)


class BrowserDirectEngine(DirectGeminiEngine):
    async def initiate_call(self, call: Call) -> EngineCallResult:
        if hasattr(self._telephony, "prepare_browser_call"):
            self._telephony.prepare_browser_call(call.id)
        result = await super().initiate_call(call)
        result.route_used = "browser"
        result.metadata = result.metadata or {}
        result.metadata["engine"] = "browser_direct"
        result.metadata["browser"] = True
        log.info(
            "browser_engine.session_started",
            call_id=str(call.id),
            session_id=result.external_id,
        )
        return result

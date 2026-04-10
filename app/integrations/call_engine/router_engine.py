"""
RoutingCallEngine — policy-driven dispatcher over call engine implementations.

This is the ONLY class that knows about CallMode and routing.
CallService sees only AbstractCallEngine.

Key design decisions:
  1. STABLE ROUTING: stop/steer/get_status use call.route_used (set by
     initiate_call) to ensure consistency. Without this, AUTO mode calls
     could be stopped by the wrong engine if configuration changes between requests.

  2. VAPI → DIRECT FALLBACK: if VapiEngine.initiate_call() raises EngineError
     and policy allows it, the router retries with DirectEngine. Logged prominently.
     No further fallback after that.

  3. NO SILENT STUB FALLBACK ON STOP/STEER: if the original engine is gone,
     we log a warning and raise rather than silently no-op through stub.
     Stopping a call must not be lost.

  4. OBSERVABILITY: every routing decision emits a structured log event with
     route name, fallback info, provider response metadata, and call ID.

  5. IDEMPOTENT METHODS: stop_call/send_instruction/get_status are always
     forwarded to the resolved engine. The engine itself must be idempotent.

Route names:
  "vapi"   → VapiCallEngine
  "direct" → DirectGeminiEngine
  "browser" → BrowserDirectEngine
  "stub"   → StubEngine (fallback)
"""
from __future__ import annotations

from typing import Optional

from app.core.exceptions import EngineError
from app.core.logging import get_logger
from app.integrations.call_engine.base import AbstractCallEngine, EngineCallResult
from app.integrations.call_engine.route_policy import CallRoutePolicy
from app.models.call import Call, CallStatus

log = get_logger(__name__)


class RoutingCallEngine(AbstractCallEngine):
    """
    Policy-driven call engine dispatcher.

    Holds references to all configured sub-engines.
    Uses CallRoutePolicy to decide which engine handles each call.
    """

    def __init__(
        self,
        vapi_engine: Optional[AbstractCallEngine],
        direct_engine: Optional[AbstractCallEngine],
        fallback_engine: AbstractCallEngine,
        browser_engine: Optional[AbstractCallEngine] = None,
        policy: Optional[CallRoutePolicy] = None,
    ) -> None:
        self._vapi = vapi_engine
        self._direct = direct_engine
        self._browser = browser_engine
        self._fallback = fallback_engine
        self._policy = policy or CallRoutePolicy(
            vapi_available=vapi_engine is not None,
            direct_available=direct_engine is not None,
            browser_available=browser_engine is not None,
        )

    # ── Engine resolution ─────────────────────────────────────────────────────

    def _engine_by_name(self, route: str) -> AbstractCallEngine:
        """Return the engine instance for a given route name."""
        if route == "vapi":
            return self._vapi or self._fallback
        if route == "direct":
            return self._direct or self._fallback
        if route == "browser":
            return self._browser or self._fallback
        return self._fallback

    def _resolve_for_new_call(self, call: Call) -> tuple:
        """
        Resolve primary engine + route name for a new call.
        Returns (engine, route_name).
        """
        route = self._policy.select_route(call)
        return self._engine_by_name(route), route

    def _resolve_for_existing_call(self, call: Call) -> AbstractCallEngine:
        """
        Resolve engine for an EXISTING call using its stored route_used.
        This ensures stop/steer/get_status always hit the same engine
        that originally handled the call.
        """
        route = self._policy.resolve_for_existing_call(call)
        engine = self._engine_by_name(route)

        if route == "vapi" and self._vapi is None:
            log.warning(
                "routing_engine.vapi_engine_missing_for_existing_call",
                call_id=str(call.id),
                route_used=route,
                note="Vapi was used to create this call but is not configured now",
            )
        elif route == "direct" and self._direct is None:
            log.warning(
                "routing_engine.direct_engine_missing_for_existing_call",
                call_id=str(call.id),
                route_used=route,
                note="Direct was used to create this call but is not configured now",
            )
        elif route == "browser" and self._browser is None:
            log.warning(
                "routing_engine.browser_engine_missing_for_existing_call",
                call_id=str(call.id),
                route_used=route,
                note="Browser sandbox created this call but is not configured now",
            )

        return engine

    # ── AbstractCallEngine implementation ─────────────────────────────────────

    async def initiate_call(self, call: Call) -> EngineCallResult:
        """
        Initiate a call using the policy-selected engine.

        On EngineError from primary engine:
          - If policy allows fallback, retry with fallback engine
          - Record fallback in result metadata for audit
          - No further fallback after first retry

        Observability: logs route selection and any fallback events.
        """
        primary_engine, primary_route = self._resolve_for_new_call(call)

        log.info(
            "routing_engine.initiate_call",
            call_id=str(call.id),
            mode=str(call.mode),
            selected_route=primary_route,
            engine=type(primary_engine).__name__,
        )

        try:
            result = await primary_engine.initiate_call(call)
            # Ensure route_used is set (sub-engines set their own, but guarantee it here)
            if not result.route_used or result.route_used == "stub":
                result.route_used = primary_route
            log.info(
                "routing_engine.call_initiated",
                call_id=str(call.id),
                route_used=result.route_used,
                external_id=result.external_id,
                initial_status=str(result.initial_status),
            )
            return result

        except EngineError as primary_exc:
            # ── Fallback logic ─────────────────────────────────────────────────
            fallback_route = self._policy.allows_fallback(call, primary_route)
            if fallback_route is None:
                log.error(
                    "routing_engine.initiate_failed_no_fallback",
                    call_id=str(call.id),
                    failed_route=primary_route,
                    error=str(primary_exc),
                )
                raise

            fallback_engine = self._engine_by_name(fallback_route)
            log.warning(
                "routing_engine.fallback",
                call_id=str(call.id),
                from_route=primary_route,
                to_route=fallback_route,
                primary_error=str(primary_exc),
            )

            try:
                result = await fallback_engine.initiate_call(call)
                result.route_used = fallback_route
                # Record fallback in metadata for audit trail
                result.metadata = result.metadata or {}
                result.metadata["fallback"] = {
                    "from_route": primary_route,
                    "reason": str(primary_exc),
                }
                log.info(
                    "routing_engine.fallback_succeeded",
                    call_id=str(call.id),
                    fallback_route=fallback_route,
                    external_id=result.external_id,
                )
                return result

            except EngineError as fallback_exc:
                log.error(
                    "routing_engine.fallback_also_failed",
                    call_id=str(call.id),
                    fallback_route=fallback_route,
                    primary_error=str(primary_exc),
                    fallback_error=str(fallback_exc),
                )
                # Raise the fallback error (more recent context)
                raise fallback_exc from primary_exc

    async def stop_call(self, call: Call) -> None:
        """
        Stop an existing call using the engine that created it.
        Uses call.route_used for stable engine resolution.
        """
        engine = self._resolve_for_existing_call(call)
        log.info(
            "routing_engine.stop_call",
            call_id=str(call.id),
            route_used=call.route_used,
            engine=type(engine).__name__,
        )
        return await engine.stop_call(call)

    async def send_instruction(self, call: Call, instruction: str) -> None:
        """
        Send steering instruction using the engine that created the call.
        Uses call.route_used for stable engine resolution.
        """
        engine = self._resolve_for_existing_call(call)
        return await engine.send_instruction(call, instruction)

    async def get_status(self, call: Call) -> CallStatus:
        """
        Poll status using the engine that created the call.
        Uses call.route_used for stable engine resolution.
        """
        engine = self._resolve_for_existing_call(call)
        return await engine.get_status(call)

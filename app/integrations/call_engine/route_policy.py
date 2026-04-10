"""
CallRoutePolicy — explicit routing policy for call engine selection.

This module answers the question "which engine should handle this call?"
and documents all policy rules in one place.

Policy rules:
  VAPI mode:
    → Use Vapi engine if configured
    → If Vapi not configured: reject (do not silently fall to stub in prod)
    → If Vapi initiation fails: optionally try Direct if fallback_to_direct=True

  DIRECT mode:
    → Use Direct engine if configured
    → If Direct not configured: reject (same principle)
    → If Direct initiation fails: reject (no further fallback)

  AUTO mode:
    → Vapi first (if configured)
    → Direct second (if configured and Vapi not available or failed)
    → Stub last (always available, safe for dev)
    → AUTO mode allows Vapi → Direct fallback (policy-driven)

Fallback rules:
  Vapi → Direct: allowed in AUTO mode or when allow_vapi_to_direct_fallback=True
  Direct → Stub: never in production (would silently swallow a failed call)
  Any → Stub in AUTO: OK (development mode or fully unconfigured system)

Route selection observability:
  Every selection emits a structured log event with:
    - selected_route
    - fallback_occurred
    - fallback_reason
    - available_engines
"""
from __future__ import annotations

from typing import Optional

from app.core.exceptions import EngineError
from app.core.logging import get_logger
from app.models.call import Call, CallMode

log = get_logger(__name__)


class CallRoutePolicy:
    """
    Determines which engine route to use for a given call.

    Instantiated once per application (in deps.py) and shared.
    All decisions are stateless and deterministic given the same settings.
    """

    def __init__(
        self,
        vapi_available: bool,
        direct_available: bool,
        browser_available: bool = False,
        allow_vapi_to_direct_fallback: bool = True,
    ) -> None:
        self._vapi_available = vapi_available
        self._direct_available = direct_available
        self._browser_available = browser_available
        self._allow_vapi_to_direct_fallback = allow_vapi_to_direct_fallback

    # ── Primary selection ─────────────────────────────────────────────────────

    def select_route(self, call: Call) -> str:
        """
        Select the primary route name for this call.

        Returns one of: "vapi", "direct", "browser", "stub"
        Logs the decision with structured context.
        """
        mode = call.mode or CallMode.AUTO
        route = self._select(mode)

        log.info(
            "route_policy.selected",
            call_id=str(call.id),
            mode=str(mode),
            selected_route=route,
            vapi_available=self._vapi_available,
            direct_available=self._direct_available,
        )
        return route

    def _select(self, mode: CallMode) -> str:
        if mode == CallMode.VAPI:
            if not self._vapi_available:
                raise EngineError(
                    "VAPI mode requested but Vapi is not configured. "
                    "Set VAPI_API_KEY, VAPI_ASSISTANT_ID, VAPI_PHONE_NUMBER_ID "
                    "or use mode=auto.",
                    detail={"mode": "vapi", "reason": "not_configured"},
                )
            return "vapi"

        if mode == CallMode.DIRECT:
            if not self._direct_available:
                raise EngineError(
                    "DIRECT mode requested but Gemini is not configured. "
                    "Set GEMINI_API_KEY or use mode=auto.",
                    detail={"mode": "direct", "reason": "not_configured"},
                )
            return "direct"

        if mode == CallMode.BROWSER:
            if not self._browser_available:
                raise EngineError(
                    "BROWSER mode requested but browser sandbox is not configured. "
                    "Set GEMINI_API_KEY and a valid Direct voice strategy.",
                    detail={"mode": "browser", "reason": "not_configured"},
                )
            return "browser"

        # AUTO: Vapi → Direct → Stub (dev-safe fallback)
        if self._vapi_available:
            return "vapi"
        if self._direct_available:
            return "direct"
        from app.core.config import settings
        if settings.is_production:
            raise EngineError(
                "Production mode: no real call engine configured. "
                "Set VAPI_API_KEY or GEMINI_API_KEY to enable a real engine. "
                "StubEngine fallback is NOT allowed in production.",
                detail={"mode": "auto", "reason": "no_real_engine_in_production"},
            )
        return "stub"

    # ── Fallback policy ───────────────────────────────────────────────────────

    def allows_fallback(self, call: Call, from_route: str) -> Optional[str]:
        """
        Determine if a fallback is allowed after a primary route failure.

        Returns the fallback route name or None if no fallback is allowed.

        Rules:
          vapi  → direct: allowed in AUTO mode or if explicitly configured
          direct → stub:  not allowed (production safety)
          stub  → any:    no further fallback
        """
        mode = call.mode or CallMode.AUTO

        if from_route == "vapi":
            if self._direct_available and (
                mode == CallMode.AUTO or self._allow_vapi_to_direct_fallback
            ):
                log.warning(
                    "route_policy.fallback_allowed",
                    call_id=str(call.id),
                    from_route="vapi",
                    to_route="direct",
                    mode=str(mode),
                )
                return "direct"

        # No further fallback — fail explicitly
        log.warning(
            "route_policy.no_fallback",
            call_id=str(call.id),
            from_route=from_route,
            mode=str(mode),
        )
        return None

    # ── Stable re-resolution for stop/steer/status ────────────────────────────

    def resolve_for_existing_call(self, call: Call) -> str:
        """
        Determine which route to use for an ALREADY-INITIATED call.

        Uses call.route_used if set (most accurate).
        Falls back to mode-based selection only if route_used is missing
        (e.g. calls created before this field was added).

        This prevents the routing inconsistency where:
          - Call was created with AUTO mode → Vapi was selected
          - Later request re-evaluates AUTO → Direct (if Vapi unconfigured)
          - stop_call incorrectly goes to Direct engine
        """
        if call.route_used:
            return call.route_used

        # Legacy fallback: infer from available IDs on the call
        if call.vapi_call_id:
            return "vapi"
        if call.mode == CallMode.BROWSER and call.mango_call_id:
            return "browser"
        if call.mango_call_id:
            return "direct"

        # Last resort: same as initial selection
        return self._select(call.mode or CallMode.AUTO)

    # ── Introspection ─────────────────────────────────────────────────────────

    @property
    def vapi_available(self) -> bool:
        return self._vapi_available

    @property
    def direct_available(self) -> bool:
        return self._direct_available

    def describe(self) -> dict:
        """Return a dict describing current policy state (for /ready endpoint)."""
        return {
            "vapi_available": self._vapi_available,
            "direct_available": self._direct_available,
            "browser_available": self._browser_available,
            "allow_vapi_to_direct_fallback": self._allow_vapi_to_direct_fallback,
        }

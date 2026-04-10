"""
AbstractCallEngine — the extension point for VapiEngine and DirectEngine.
Any call engine must implement this interface. CallService depends only on
this abstraction, making engine swaps a one-line change in the DI layer.

Route names (route_used field):
  "vapi"   — Vapi API route (SIP via Vapi → Mango trunk)
  "direct" — Direct route (Gemini Live + TelephonyAdapter)
  "browser" — Browser QA route (Gemini Live + BrowserAudioBridge)
  "stub"   — Stub/no-op (development, testing)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from app.models.call import Call, CallStatus


@dataclass
class EngineCallResult:
    """
    Returned by initiate_call to pass engine-specific identifiers back.

    Fields:
      external_id      — engine-assigned call ID (Vapi call ID, Direct session ID)
      initial_status   — status immediately after initiation
      route_used       — which route was selected: "vapi", "direct", "stub"
      telephony_leg_id — provider-level telephony leg identifier (SIP call-id,
                         Mango leg ID, etc.). Stored on Call for ID mapping.
      provider_metadata — raw response from the telephony provider (for audit)
      metadata         — other engine-specific data
    """
    external_id: Optional[str] = None
    initial_status: CallStatus = CallStatus.QUEUED
    route_used: str = "stub"
    telephony_leg_id: Optional[str] = None
    provider_metadata: Optional[dict] = None
    metadata: Optional[dict] = None


class AbstractCallEngine(ABC):
    """
    Interface all call engines must implement.

    Implementations:
      StubEngine          — no-op for development/testing
      VapiCallEngine      — Vapi API route
      DirectGeminiEngine  — Direct Gemini Live route
      RoutingCallEngine   — dispatcher (selects sub-engine by call.mode)
    """

    @abstractmethod
    async def initiate_call(self, call: Call) -> EngineCallResult:
        """
        Start the outbound call for the given Call record.
        Must not mutate the Call object — that's CallService's responsibility.
        Returns EngineCallResult with engine-assigned IDs and route info.
        """
        ...

    @abstractmethod
    async def stop_call(self, call: Call) -> None:
        """
        Terminate an in-progress call immediately.
        Must be idempotent — calling it on an already-stopped call is safe.
        """
        ...

    @abstractmethod
    async def send_instruction(self, call: Call, instruction: str) -> None:
        """
        Deliver a real-time steering instruction to the AI during the call.
        No-op if the engine doesn't support live steering.
        """
        ...

    @abstractmethod
    async def get_status(self, call: Call) -> CallStatus:
        """
        Poll the engine for the current call status.
        Used for reconciliation when webhooks are missed.
        """
        ...

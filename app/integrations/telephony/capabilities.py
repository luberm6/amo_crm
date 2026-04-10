"""
ProviderCapabilities — capability declaration for telephony adapters.

Each AbstractTelephonyAdapter implementation exposes a `capabilities` property
that returns a ProviderCapabilities instance.  The rest of the system uses
this to make capability-aware decisions without knowing the concrete provider.

Usage:
  adapter = registry.resolve("auto")
  adapter.capabilities.check("audio_stream")    # raises if not supported
  if adapter.capabilities.supports_bridge:
      await adapter.bridge_legs(...)
  else:
      # degraded: notify operator, use alternative

Capability flags:
  supports_outbound_call         — originate_call() makes a real call
  supports_audio_stream          — audio_stream() / send_audio() work (bidirectional PCM)
  supports_bridge                — bridge_legs() connects two legs
  supports_whisper               — play_whisper() delivers TTS to one leg
  supports_call_recording_events — provider fires call-state webhooks (RINGING/ANSWERED/etc.)
  supports_sip_trunk             — SIP-level integration available
  supports_real_time_events      — events arrive via webhook (vs polling only)

Fallback / degraded mode:
  If a required capability is missing:
    - call capabilities.check(feature) before using it
    - UnsupportedOperationError is raised (HTTP 422) — caller decides to degrade or abort
    - Never silently ignore a missing capability — that leads to phantom calls
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.core.exceptions import EngineError


class UnsupportedOperationError(EngineError):
    """
    Raised when a telephony operation is not supported by the configured provider.

    Signals a configuration mismatch: the caller asked for a feature the
    provider does not implement.  The caller may:
    - Abort with a user-visible error (HTTP 422)
    - Fall back to a degraded mode if the operation is optional
    - Switch to a different provider (not automatic)
    """
    error_code = "unsupported_operation"
    status_code = 422


@dataclass
class ProviderCapabilities:
    """
    Declares what a telephony provider supports.

    All `supports_*` fields default to False — subclasses must explicitly
    opt in to each capability.  This follows the principle of least privilege:
    an unimplemented capability is never silently assumed.

    Fields:
      provider_name               — human-readable provider identifier
      supports_outbound_call      — originate_call() makes a real outbound call
      supports_audio_stream       — bidirectional PCM streaming for Direct/Gemini mode
      supports_bridge             — bridge two call legs (warm transfer)
      supports_whisper            — play TTS whisper to manager before bridging
      supports_call_recording_events — provider fires call-state webhook events
      supports_sip_trunk          — SIP-level integration (not just REST API)
      supports_real_time_events   — events arrive as webhooks (vs polling required)
      supports_audio_bridge       — attach_audio_bridge() / detach_audio_bridge() work
      max_concurrent_calls        — optional limit (None = unknown / unlimited)
      notes                       — human-readable capability notes for documentation
    """
    provider_name: str
    supports_outbound_call: bool = False
    supports_audio_stream: bool = False
    supports_bridge: bool = False
    supports_whisper: bool = False
    supports_call_recording_events: bool = False
    supports_sip_trunk: bool = False
    supports_real_time_events: bool = False
    supports_audio_bridge: bool = False
    max_concurrent_calls: Optional[int] = None
    notes: str = ""

    def check(self, feature: str) -> None:
        """
        Assert that a feature is supported by this provider.

        Raises UnsupportedOperationError if `supports_{feature}` is False.

        Example:
            adapter.capabilities.check("audio_stream")
            # → raises if not supported, otherwise continues

        Use before any optional telephony operation to fail fast with a
        meaningful error rather than a cryptic NotImplementedError deep
        in the call stack.
        """
        attr = f"supports_{feature}"
        if not getattr(self, attr, False):
            raise UnsupportedOperationError(
                f"Provider '{self.provider_name}' does not support '{feature}'. "
                f"Configure a provider that supports this feature or use a degraded mode.",
                detail={
                    "provider": self.provider_name,
                    "unsupported_feature": feature,
                    "notes": self.notes,
                },
            )

    def to_dict(self) -> dict:
        """Serialize to dict for /ready endpoint and observability."""
        return {
            "provider_name": self.provider_name,
            "supports_outbound_call": self.supports_outbound_call,
            "supports_audio_stream": self.supports_audio_stream,
            "supports_bridge": self.supports_bridge,
            "supports_whisper": self.supports_whisper,
            "supports_call_recording_events": self.supports_call_recording_events,
            "supports_sip_trunk": self.supports_sip_trunk,
            "supports_real_time_events": self.supports_real_time_events,
            "supports_audio_bridge": self.supports_audio_bridge,
            "max_concurrent_calls": self.max_concurrent_calls,
            "notes": self.notes,
        }

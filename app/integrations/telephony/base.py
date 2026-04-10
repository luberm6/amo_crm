"""
AbstractTelephonyAdapter — production interface for telephony operations.

Covers both use cases:
  Direct mode (Gemini Live + audio streaming) — full contract
  Mango Click-to-Call — originate/terminate/bridge/whisper without audio stream

Two operation families:

A. AUDIO CHANNEL (Direct/Gemini mode):
   connect()      — originate outbound call, return TelephonyChannel
   disconnect()   — terminate call (idempotent)
   audio_stream() — async generator: PCM from customer (16kHz mono 16bit)
   send_audio()   — send PCM to customer

B. TELEPHONY CONTROL (Mango/SIP mode, warm transfer):
   originate_call() — SIP/API-level outbound call origination
   bridge_legs()    — bridge two call legs (customer ↔ manager)
   play_whisper()   — play whisper TTS to manager before bridge
   terminate_leg()  — hang up a specific call leg
   get_leg_state()  — inspect current state of a call leg

Known limitations by implementation:
  StubTelephonyAdapter   — all methods stubbed, no real telephony
  MangoTelephonyAdapter  — originate/terminate/bridge/whisper via Mango API;
                           audio_stream/send_audio require SIP UA (Phase 2)

SIP integration notes:
  - Answer events in SIP can be delayed 2-10s after INVITE acceptance
  - Call leg IDs (Call-ID header) ≠ Mango internal call IDs — track both
  - Bridge failures must be caught and both legs terminated
  - Duplicate RINGING/ANSWER events are normal — handle idempotently
  - Hang-up ordering: Mango sends BYE before we get the event; check state first
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, AsyncIterator, Optional

if TYPE_CHECKING:
    from app.integrations.telephony.audio_bridge import AbstractAudioBridge
    from app.integrations.telephony.capabilities import ProviderCapabilities


class TelephonyLegState(str, Enum):
    """State of a single telephony call leg."""
    INITIATING = "initiating"  # INVITE sent, waiting for answer
    RINGING = "ringing"        # 180 Ringing received
    ANSWERED = "answered"      # 200 OK — media active
    BRIDGED = "bridged"        # Leg bridged to another leg
    TERMINATING = "terminating"  # BYE sent/received
    TERMINATED = "terminated"  # Leg fully cleared
    FAILED = "failed"          # No answer, error, unreachable


@dataclass
class TelephonyChannel:
    """
    Handle for an active audio connection with a customer.

    channel_id    — adapter-specific connection identifier
    phone         — E.164 customer number
    sip_call_id   — SIP Call-ID header value (for correlation with SIP logs)
    provider_leg_id — provider's internal leg ID (e.g. Mango leg_id)
    state         — current leg state
    metadata      — arbitrary provider data (SIP session handle, etc.)
    """
    channel_id: str
    phone: str
    sip_call_id: Optional[str] = None
    provider_leg_id: Optional[str] = None
    state: TelephonyLegState = TelephonyLegState.INITIATING
    metadata: Optional[dict] = field(default=None)


@dataclass
class TelephonyOriginateResult:
    """
    Result of originate_call().
    Returned before the call is answered — state transitions via callbacks/polling.
    """
    leg_id: str                 # Provider leg ID for subsequent operations
    sip_call_id: Optional[str] = None
    provider_response: Optional[dict] = None


class AbstractTelephonyAdapter(ABC):
    """
    Production interface for telephony operations.

    CallService / DirectGeminiEngine use this adapter to:
    - Make outbound calls to customers
    - Stream audio bidirectionally (for Gemini Live)
    - Bridge call legs during warm transfer
    - Play whisper to manager before handoff

    Every implementation must declare its capabilities via the `capabilities`
    property.  Before calling any optional method, check:
        adapter.capabilities.check("audio_stream")

    Implementations must be idempotent:
    - disconnect() on already-terminated channel: no error
    - terminate_leg() on missing leg: no error
    - bridge_legs() must verify legs are in ANSWERED state first

    Error handling:
    - SIP errors (no answer, busy, unreachable) → raise TelephonyError
    - Network errors → raise TelephonyError
    - Missing leg on terminate/bridge → log + return (idempotent)
    - Unsupported operation → raise UnsupportedOperationError (via capabilities.check)
    """

    # ── Capability declaration ────────────────────────────────────────────────

    @property
    @abstractmethod
    def capabilities(self) -> "ProviderCapabilities":
        """
        Declare what this provider supports.

        Returns a ProviderCapabilities instance with all `supports_*` flags
        set correctly for this implementation.  Used by the registry, routing
        logic, and callers to make capability-aware decisions before invoking
        optional methods.
        """
        ...

    # ── Audio channel API (used by DirectGeminiEngine) ────────────────────────

    @abstractmethod
    async def connect(self, phone: str) -> TelephonyChannel:
        """
        Initiate an outbound call to phone, return an audio channel handle.

        The channel is created synchronously but the call may not be answered yet.
        Use audio_stream() to wait for actual audio data (implies answered state).

        In stub: returns immediately with synthetic channel, no real call.
        In Mango: uses originate_call() + wait for ANSWERED event.
        """
        ...

    @abstractmethod
    async def disconnect(self, phone: str) -> None:
        """
        Terminate the call. Idempotent.

        In production: sends BYE / Mango terminate API.
        In stub: removes channel from internal state.
        """
        ...

    @abstractmethod
    async def audio_stream(
        self, channel: TelephonyChannel
    ) -> AsyncIterator[bytes]:
        """
        Async generator: yield PCM chunks from customer (16kHz mono 16bit).

        Yields continuously while call is active.
        If no audio: yield silence (640 bytes = 20ms) to keep Gemini alive.
        Terminates when customer hangs up or disconnect() is called.

        Stub: yields silence indefinitely.
        Mango Phase 2: receives RTP via SIP media leg.
        """
        ...

    @abstractmethod
    async def send_audio(
        self, channel: TelephonyChannel, pcm_bytes: bytes
    ) -> None:
        """
        Send PCM audio to the customer (from Gemini TTS or ElevenLabs).

        pcm_bytes — 16kHz mono 16bit PCM (or 24kHz if ElevenLabs).
        Adapter handles resampling if provider requires different format.

        Stub: discards audio (logged at DEBUG level).
        Mango Phase 2: sends RTP via SIP media leg.
        """
        ...

    @abstractmethod
    async def attach_audio_bridge(
        self, channel: TelephonyChannel
    ) -> "AbstractAudioBridge":
        """
        Create and open a session-scoped audio bridge for this channel.

        Called once at Direct session start. Returns the bridge; caller owns lifecycle.
        Providers without audio capability: raise UnsupportedOperationError.
        Stub: return SilenceAudioBridge (yields silence indefinitely).

        The bridge separates audio session lifecycle from adapter control plane,
        avoiding scattered audio buffer state across the adapter.
        """
        ...

    @abstractmethod
    async def detach_audio_bridge(self, bridge: "AbstractAudioBridge") -> None:
        """Close and release the audio bridge. Idempotent."""
        ...

    # ── Telephony control API (used for warm transfer, Mango SIP) ────────────

    @abstractmethod
    async def originate_call(
        self,
        phone: str,
        caller_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> TelephonyOriginateResult:
        """
        Originate an outbound SIP/API call at the telephony provider level.

        This is the lower-level call compared to connect().
        Returns immediately with a leg_id — the call is not yet answered.

        Use cases:
        - Mango Click-to-Call: POST /commands/callback
        - Direct SIP INVITE

        Raises TelephonyError on provider-level failures.
        """
        ...

    @abstractmethod
    async def bridge_legs(
        self,
        customer_leg_id: str,
        manager_leg_id: str,
    ) -> None:
        """
        Bridge two call legs (customer ↔ manager) for warm transfer.

        Both legs must be in ANSWERED state before bridging.
        If either leg is terminated, raises TelephonyError.

        SIP note: bridge is typically implemented via SIP re-INVITE or
        Mango's transfer API. Mango sends duplicate events during bridge —
        process them idempotently.
        """
        ...

    @abstractmethod
    async def play_whisper(
        self,
        leg_id: str,
        message: str,
    ) -> None:
        """
        Play a whisper message to manager before bridging to customer.

        message — text to TTS (provider handles synthesis) or audio URL.
        Should be called after manager answers, before bridge_legs().

        In stub: logs and returns.
        In Mango: POST /commands/play (or equivalent).
        """
        ...

    @abstractmethod
    async def terminate_leg(self, leg_id: str) -> None:
        """
        Terminate a specific call leg. Idempotent.

        Called when:
        - Transfer fails (terminate manager leg)
        - Direct engine stops
        - Error recovery

        In stub: logs and returns.
        In Mango: POST /commands/hangup.
        """
        ...

    @abstractmethod
    async def get_leg_state(self, leg_id: str) -> TelephonyLegState:
        """
        Return the current state of a call leg.

        Used for:
        - Verifying legs are ANSWERED before bridge
        - Reconciliation after delayed SIP events

        In stub: returns ANSWERED (simulates active call).
        In Mango: GET /stats/request or webhook state tracking.
        """
        ...

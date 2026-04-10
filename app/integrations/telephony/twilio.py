"""
TwilioTelephonyAdapter — skeletal adapter for Twilio Voice.

STATUS: SKELETAL — all methods raise NotImplementedError.
This file serves as a template for adding a new telephony provider.

To implement:
  1. Install twilio SDK: pip install twilio
  2. Add config fields: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER
  3. Implement each method marked with # TODO below
  4. Update capabilities.supports_* flags to True as methods are implemented
  5. Register in build_default_registry() when TWILIO credentials are configured

Twilio Voice capability overview (for reference when implementing):
  originate_call   — Twilio REST API: POST /Accounts/{SID}/Calls.json
  terminate_leg    — Twilio REST API: POST /Accounts/{SID}/Calls/{CallSid}.json {Status: "completed"}
  bridge_legs      — Twilio TwiML Conference or Dial Transfer
  play_whisper     — Twilio TwiML whisper participant in conference
  audio_stream     — Twilio Media Streams (WebSocket): wss://... per-call stream
  get_leg_state    — Twilio REST GET /Calls/{CallSid}.json → status field
  webhooks         — Twilio StatusCallback webhook per call

Twilio event normalization:
  Twilio CallStatus: queued → initiated → ringing → in-progress → completed/failed/busy/no-answer
  Map to TelephonyLegState in normalize_event() (TODO)

Environment variables (add to .env.example when implementing):
  TWILIO_ACCOUNT_SID   — from Twilio Console → Account Info
  TWILIO_AUTH_TOKEN    — from Twilio Console → Account Info
  TWILIO_FROM_NUMBER   — E.164 purchased Twilio number (e.g. +12015551234)
  TWILIO_TWIML_APP_SID — (optional) TwiML App SID for Media Streams
"""
from __future__ import annotations

from typing import TYPE_CHECKING, AsyncIterator, Optional

if TYPE_CHECKING:
    from app.integrations.telephony.audio_bridge import AbstractAudioBridge

from app.core.logging import get_logger
from app.integrations.telephony.base import (
    AbstractTelephonyAdapter,
    TelephonyChannel,
    TelephonyLegState,
    TelephonyOriginateResult,
)
from app.integrations.telephony.capabilities import ProviderCapabilities

log = get_logger(__name__)


class TwilioTelephonyAdapter(AbstractTelephonyAdapter):
    """
    Skeletal Twilio Voice adapter.

    All telephony operations raise NotImplementedError until implemented.
    The capabilities property accurately declares what Twilio supports
    conceptually so route planning / capability checks work even before
    the implementation is complete.

    To add Twilio support:
      1. Read the docstring at the top of this file
      2. Implement each TODO method below
      3. Set the corresponding `supports_*` flag to True in capabilities
      4. Add TWILIO_* settings to app/core/config.py
      5. Register in registry.py:build_default_registry()
      6. Add tests in tests/test_telephony_twilio.py
    """

    def __init__(self) -> None:
        # TODO: initialise twilio.rest.Client(account_sid, auth_token)
        # self._client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
        # self._from_number = settings.twilio_from_number
        self._leg_states: dict = {}

    # ── Capability declaration ────────────────────────────────────────────────

    @property
    def capabilities(self) -> ProviderCapabilities:
        """
        Twilio Voice capability declaration.

        These flags reflect what Twilio *can* support once fully implemented.
        As each method is implemented, the flag stays True.
        If a method is explicitly not planned, set to False.
        """
        return ProviderCapabilities(
            provider_name="twilio",
            supports_outbound_call=True,          # TODO: via REST /Calls.json
            supports_audio_stream=True,           # TODO: via Twilio Media Streams WebSocket
            supports_bridge=True,                 # TODO: via TwiML Conference
            supports_whisper=True,                # TODO: via Conference whisper participant
            supports_call_recording_events=True,  # TODO: via StatusCallback webhooks
            supports_sip_trunk=True,              # Twilio SIP Trunking is supported
            supports_real_time_events=True,       # TODO: StatusCallback webhook per call
            notes=(
                "Skeletal adapter — all methods raise NotImplementedError. "
                "Implement using twilio-python SDK. "
                "See module docstring for step-by-step guide."
            ),
        )

    # ── Audio channel API ─────────────────────────────────────────────────────

    async def connect(self, phone: str) -> TelephonyChannel:
        """
        TODO: Initiate outbound call via Twilio REST API and return channel handle.

        Implementation guide:
          result = await self.originate_call(phone)
          channel = TelephonyChannel(
              channel_id=result.leg_id,
              phone=phone,
              sip_call_id=result.sip_call_id,
              provider_leg_id=result.leg_id,
              state=TelephonyLegState.INITIATING,
              metadata=result.provider_response,
          )
          return channel
        """
        raise NotImplementedError(
            "TwilioTelephonyAdapter.connect() is not implemented. "
            "See module docstring for implementation guide."
        )

    async def disconnect(self, phone: str) -> None:
        """
        TODO: Terminate active call for phone via Twilio REST API.

        Implementation guide:
          Find the CallSid by phone from self._leg_states or a local index.
          POST /Accounts/{SID}/Calls/{CallSid}.json with Status=completed.
        """
        raise NotImplementedError(
            "TwilioTelephonyAdapter.disconnect() is not implemented."
        )

    async def audio_stream(
        self, channel: TelephonyChannel
    ) -> AsyncIterator[bytes]:
        """
        TODO: Stream bidirectional PCM audio via Twilio Media Streams.

        Implementation guide:
          1. Configure TwiML to start a Media Stream on the call
          2. Accept WebSocket connection at your server endpoint
          3. Receive mulaw-8kHz audio from Twilio, convert to PCM 16kHz
          4. yield PCM chunks (640 bytes = 20ms @ 16kHz)
          5. Implement send_audio() to write PCM back via the WebSocket

        Twilio sends audio in mulaw 8kHz. Resampling to 16kHz is required.
        """
        raise NotImplementedError(
            "TwilioTelephonyAdapter.audio_stream() is not implemented. "
            "Requires Twilio Media Streams WebSocket integration."
        )
        yield b""  # noqa: unreachable

    async def send_audio(
        self, channel: TelephonyChannel, pcm_bytes: bytes
    ) -> None:
        """
        TODO: Send PCM audio to customer via Twilio Media Streams.

        Implementation guide:
          Convert PCM 16kHz → mulaw 8kHz.
          Send via Media Streams WebSocket as base64-encoded mulaw.
        """
        raise NotImplementedError(
            "TwilioTelephonyAdapter.send_audio() is not implemented."
        )

    async def attach_audio_bridge(
        self, channel: "TelephonyChannel"
    ) -> "AbstractAudioBridge":
        """
        TODO: Attach a Twilio Media Streams bridge.

        Implementation guide:
          Create a WebSocket connection to Twilio Media Streams endpoint.
          Setup: TTS → mulaw 8kHz → MediaStream events.
        """
        raise NotImplementedError(
            "TwilioTelephonyAdapter.attach_audio_bridge() is not implemented."
        )

    async def detach_audio_bridge(self, bridge: "AbstractAudioBridge") -> None:
        """
        TODO: Detach Twilio Media Streams bridge.

        Implementation guide:
          Close WebSocket connection gracefully.
        """
        raise NotImplementedError(
            "TwilioTelephonyAdapter.detach_audio_bridge() is not implemented."
        )

    # ── Telephony control API ─────────────────────────────────────────────────

    async def originate_call(
        self,
        phone: str,
        caller_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> TelephonyOriginateResult:
        """
        TODO: Make outbound call via Twilio REST API.

        Implementation guide:
          from_number = caller_id or self._from_number
          call = self._client.calls.create(
              to=phone,
              from_=from_number,
              url="https://your-server/twiml/outbound",  # TwiML webhook
              status_callback="https://your-server/webhooks/twilio",
              status_callback_event=["initiated","ringing","answered","completed"],
          )
          return TelephonyOriginateResult(
              leg_id=call.sid,
              sip_call_id=None,
              provider_response={"call_sid": call.sid, "status": call.status},
          )
        """
        raise NotImplementedError(
            "TwilioTelephonyAdapter.originate_call() is not implemented. "
            "See module docstring for implementation guide."
        )

    async def bridge_legs(
        self,
        customer_leg_id: str,
        manager_leg_id: str,
    ) -> None:
        """
        TODO: Bridge customer and manager via Twilio Conference.

        Implementation guide (option A — TwiML Conference):
          1. Add both calls to a named conference room
          2. Customer: <Dial><Conference>conf-{call_id}</Conference></Dial>
          3. Manager: <Dial><Conference>conf-{call_id}</Conference></Dial>

        Implementation guide (option B — warm transfer):
          Use Twilio Call Transfer: POST /Calls/{CallSid}.json with TwiML
          that dials the manager and bridges.
        """
        raise NotImplementedError(
            "TwilioTelephonyAdapter.bridge_legs() is not implemented."
        )

    async def play_whisper(self, leg_id: str, message: str) -> None:
        """
        TODO: Play TTS whisper to manager before bridging.

        Implementation guide:
          In TwiML Conference, use a separate participant with muted=true
          for the whisper and coach=<manager_call_sid> parameter.
          See Twilio Conference whisper documentation.
        """
        raise NotImplementedError(
            "TwilioTelephonyAdapter.play_whisper() is not implemented."
        )

    async def terminate_leg(self, leg_id: str) -> None:
        """
        TODO: Terminate a Twilio call leg. Idempotent.

        Implementation guide:
          try:
              self._client.calls(leg_id).update(status="completed")
          except TwilioRestException as exc:
              if exc.status == 404:
                  pass  # Already terminated
              else:
                  raise TelephonyError(str(exc)) from exc
          self._leg_states[leg_id] = TelephonyLegState.TERMINATED
        """
        raise NotImplementedError(
            "TwilioTelephonyAdapter.terminate_leg() is not implemented."
        )

    async def get_leg_state(self, leg_id: str) -> TelephonyLegState:
        """
        TODO: Return current state of a Twilio call leg.

        Implementation guide:
          cached = self._leg_states.get(leg_id)
          if cached:
              return cached
          call = self._client.calls(leg_id).fetch()
          return _TWILIO_STATE_MAP.get(call.status, TelephonyLegState.FAILED)
        """
        raise NotImplementedError(
            "TwilioTelephonyAdapter.get_leg_state() is not implemented."
        )


# ── Twilio state mapping (reference — use when implementing get_leg_state) ──────

# Twilio CallStatus → TelephonyLegState
_TWILIO_STATE_MAP: dict = {
    "queued": TelephonyLegState.INITIATING,
    "initiated": TelephonyLegState.INITIATING,
    "ringing": TelephonyLegState.RINGING,
    "in-progress": TelephonyLegState.ANSWERED,
    "completed": TelephonyLegState.TERMINATED,
    "failed": TelephonyLegState.FAILED,
    "busy": TelephonyLegState.FAILED,
    "no-answer": TelephonyLegState.FAILED,
    "canceled": TelephonyLegState.TERMINATED,
}

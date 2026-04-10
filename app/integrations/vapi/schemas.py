"""
Pydantic schemas for Vapi webhook message payloads.
Vapi POSTs JSON to our server URL. The top-level structure is:
  { "message": { "type": "...", ...event-specific fields } }
We use lenient parsing (extra="allow") so unknown fields don't break
the system when Vapi adds new event properties.
Vapi event type reference:
  https://docs.vapi.ai/server-url/events
"""
from __future__ import annotations
from typing import Optional
from enum import Enum
from pydantic import BaseModel, Field
class VapiMessageType(str, Enum):
    """All known Vapi server message types."""
    # Real-time transcript chunk
    TRANSCRIPT = "transcript"
    # Call lifecycle status change
    STATUS_UPDATE = "status-update"
    # Full call report sent when call ends
    END_OF_CALL_REPORT = "end-of-call-report"
    # Speech detection events
    SPEECH_UPDATE = "speech-update"
    # AI requested a function/tool call
    TOOL_CALLS = "tool-calls"
    FUNCTION_CALL = "function-call"
    # Transfer destination needed — triggers NEEDS_TRANSFER flow
    TRANSFER_DESTINATION_REQUEST = "transfer-destination-request"
    # Vapi needs to know which assistant to use (dynamic assistants)
    ASSISTANT_REQUEST = "assistant-request"
    # Full conversation history update
    CONVERSATION_UPDATE = "conversation-update"
    # Voice/audio data (not typically used by backends)
    VOICE_INPUT = "voice-input"
    # Call was hung up by user
    HANG = "hang"
    # User interrupted the AI
    USER_INTERRUPTED = "user-interrupted"
    # Phone call ringing/connecting events
    PHONE_CALL_CONTROL = "phone-call-control"
class VapiCallStatus(str, Enum):
    """Vapi call status values (from status-update events)."""
    QUEUED = "queued"
    RINGING = "ringing"
    IN_PROGRESS = "in-progress"
    FORWARDING = "forwarding"
    ENDED = "ended"
class VapiCallObject(BaseModel):
    """Embedded call object present in most Vapi events."""
    model_config = {"extra": "allow"}
    id: str
    status: Optional[str] = None
    ended_reason: Optional[str] = Field(None, alias="endedReason")
    phone_number_id: Optional[str] = Field(None, alias="phoneNumberId")
    assistant_id: Optional[str] = Field(None, alias="assistantId")
    started_at: Optional[str] = Field(None, alias="startedAt")
    ended_at: Optional[str] = Field(None, alias="endedAt")
    # Our internal call_id passed via metadata — used for correlation
    metadata: Optional[dict] = None
class VapiTranscriptMessage(BaseModel):
    """Emitted during a call for each utterance chunk."""
    model_config = {"extra": "allow"}
    type: str
    role: str  # "assistant" | "user" | "system"
    transcript: str
    # "partial" during speech, "final" when utterance is complete
    transcript_type: Optional[str] = Field(None, alias="transcriptType")
    call: Optional[VapiCallObject] = None
    timestamp: Optional[float] = None
class VapiStatusUpdateMessage(BaseModel):
    """Emitted when the call transitions to a new Vapi lifecycle state."""
    model_config = {"extra": "allow"}
    type: str
    status: VapiCallStatus
    call: Optional[VapiCallObject] = None
class VapiConversationMessage(BaseModel):
    """Single message entry in an end-of-call-report messages array."""
    model_config = {"extra": "allow"}
    role: str
    message: Optional[str] = None  # text of the utterance
    content: Optional[str] = None  # alternative field name
    time: Optional[float] = None
    end_time: Optional[float] = None
    seconds_from_start: Optional[float] = None
    @property
    def text(self) -> str:
        return self.message or self.content or ""
class VapiEndOfCallMessage(BaseModel):
    """Full call report — sent once when the call terminates."""
    model_config = {"extra": "allow"}
    type: str
    ended_reason: Optional[str] = Field(None, alias="endedReason")
    call: Optional[VapiCallObject] = None
    # Full transcript as a single string (Vapi-generated)
    transcript: Optional[str] = None
    # Structured conversation messages (preferred over transcript string)
    messages: Optional[list[dict]] = None
    # AI-generated summary
    summary: Optional[str] = None
    # Analysis block — may contain successEvaluation, sentiment, etc.
    analysis: Optional[dict] = None
    recording_url: Optional[str] = Field(None, alias="recordingUrl")
    stereo_recording_url: Optional[str] = Field(None, alias="stereoRecordingUrl")
class VapiToolCallsMessage(BaseModel):
    """Emitted when the AI triggers a function/tool call."""
    model_config = {"extra": "allow"}
    type: str
    tool_calls: Optional[list[dict]] = Field(None, alias="toolCalls")
    call: Optional[VapiCallObject] = None
class VapiTransferDestinationRequest(BaseModel):
    """Emitted when the AI decides to transfer the call."""
    model_config = {"extra": "allow"}
    type: str
    call: Optional[VapiCallObject] = None
class VapiWebhookEnvelope(BaseModel):
    """Top-level wrapper for all Vapi server messages."""
    message: dict[str, Any]
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models.call import CallStatus
from app.schemas.transcript import TranscriptEntryRead


class BrowserCallCreate(BaseModel):
    label: str = Field(default="sandbox", min_length=1, max_length=40)
    agent_profile_id: Optional[uuid.UUID] = None


class BrowserCallStartResponse(BaseModel):
    call_id: uuid.UUID
    status: CallStatus
    session_id: str
    agent_profile_id: Optional[uuid.UUID] = None
    browser_token: str
    websocket_url: str
    status_url: str
    stop_url: str
    voice_strategy: str
    active_voice_path: str
    fallback_voice_path: Optional[str] = None


class BrowserCallDebugRead(BaseModel):
    session_id: Optional[str] = None
    voice_strategy: Optional[str] = None
    active_voice_path: Optional[str] = None
    primary_voice_path: Optional[str] = None
    fallback_voice_path: Optional[str] = None
    fallback_used: bool = False
    session_mode: Optional[str] = None
    websocket_connected: bool = False
    bridge_open: bool = False
    inbound_chunks_received: int = 0
    inbound_chunks_sent_to_model: int = 0
    outbound_chunks_played: int = 0
    model_response_latency_ms_last: Optional[float] = None
    tts_latency_ms_last: Optional[float] = None
    outbound_playback_latency_ms_last: Optional[float] = None
    tts_first_chunk_sent_ms_last: Optional[float] = None
    tts_last_chunk_received_ms_last: Optional[float] = None
    tts_audio_duration_ms_last: Optional[float] = None
    tts_leading_silence_trimmed_ms_last: Optional[float] = None
    tts_trailing_silence_trimmed_ms_last: Optional[float] = None
    tts_chunks_in_last: int = 0
    tts_chunks_out_last: int = 0
    tts_tiny_chunks_in_last: int = 0
    tts_turn_id_last: Optional[str] = None
    last_error: Optional[str] = None
    last_failure_stage: Optional[str] = None
    last_disconnect_reason: Optional[str] = None


class BrowserCallRead(BaseModel):
    call_id: uuid.UUID
    status: CallStatus
    label: str
    agent_profile_id: Optional[uuid.UUID] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    transcript_entries: list[TranscriptEntryRead] = Field(default_factory=list)
    debug: BrowserCallDebugRead


class BrowserCallDebugActionRead(BaseModel):
    ok: bool = True
    action: str
    message: str
    chunks_enqueued: int = 0

# Real Audio Loop (Direct Mode)

## Goal
First practical AI voice loop in existing stack:

customer speech -> FreeSWITCH bridge -> backend -> Gemini -> audio response -> FreeSWITCH bridge -> customer

No architecture rewrite; uses:
- `DirectGeminiEngine`
- `DirectSessionManager`
- `GeminiLiveClient`
- `ElevenLabsClient` (fallback)

## Input Path
1. `FreeSwitchAudioBridge.audio_in()` yields real audio frames from media gateway.
2. `DirectSessionManager._bridge_audio_reader()` chunks frames to fixed size.
3. Frames enter bounded inbound queue (`audio_in_queue`) with drop accounting.
4. `_drain_audio_in_queue()` sends chunks to `GeminiLiveClient.send_audio(...)` when input capability is enabled.

### Input timeout / disconnect behavior
- If bridge stream closes (caller hangup path), session manager sets stop flag and triggers `terminate_session`.
- This stops Gemini loop and closes bridge/resources.

## Output Path
### A) Gemini native audio
- Condition: `GEMINI_AUDIO_OUTPUT_ENABLED=true`.
- `GeminiLiveClient` receives audio parts from server and calls `on_audio`.
- Session manager enqueues chunks to outbound queue, then writes to `audio_bridge.audio_out`.

### B) ElevenLabs fallback
- Condition: native Gemini audio disabled and ElevenLabs configured.
- Assistant text callback starts TTS task (`voice.synthesize_streaming`).
- First-chunk latency is measured.
- Chunks are enqueued to outbound queue and written to bridge.

## Capability Modes
- `text_only`
- `audio_in_only`
- `audio_out_only`
- `full_duplex`

Additional runtime flags:
- `real_audio_in`
- `real_audio_out`
- `real_full_duplex`

`real_*` is true only when bridge is real FreeSWITCH path (`esl_rtp`), not mock.

## Latency / Backpressure Points
- inbound queue latency
- model response latency
- TTS first chunk latency
- outbound playback latency
- inbound/outbound queue drops

Export:
- per-session metrics via `get_session_metrics`
- process metrics via `/metrics`

## What Works Now
- Real bridge wiring from FreeSWITCH path into Direct session loop.
- Inbound queue -> Gemini audio send path.
- Native audio and fallback TTS output paths into bridge.
- Steering during live session remains active.
- Transfer hook can suspend AI audio.
- Session cleanup covers bg loop, bridge reader, TTS tasks, Gemini WS, bridge close.

## Known Incomplete Areas
- Live tenant E2E confirmation of audible output on PSTN is still required.
- Full-duplex quality under jitter/NAT/SRTP constraints not yet production-validated.
- Some runtime warnings in unrelated tests still exist and should be cleaned separately.

## Manual Smoke Checklist
1. Enable real bridge mode:
   - `MEDIA_GATEWAY_ENABLED=true`
   - `MEDIA_GATEWAY_MODE=esl_rtp`
2. Enable Direct audio:
   - `GEMINI_AUDIO_INPUT_ENABLED=true`
   - either `GEMINI_AUDIO_OUTPUT_ENABLED=true` or ElevenLabs credentials.
3. Place call in Direct mode and confirm session capability is not `text_only`.
4. Speak as caller: verify inbound chunk counters increase.
5. Confirm Gemini receives audio (model response latency updates).
6. Confirm caller hears AI voice (native audio or fallback TTS path).
7. Trigger `/stop`: ensure call/session terminate and bridge closes.
8. Trigger warm transfer: ensure AI audio is suspended and resources released.

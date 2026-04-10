# Direct Mode Runtime

## Overview
Direct route now supports explicit capability-based orchestration:
- `text_only`
- `audio_in_only`
- `audio_out_only`
- `full_duplex`

This is runtime classification, not a marketing status.

Voice strategy is configured separately:
- `disabled`
- `gemini_primary`
- `tts_primary`
- `experimental_hybrid`

See [voice_strategy.md](/Users/iluxa/Amo_crm/docs/voice_strategy.md).

## Orchestration Changes
- `DirectSessionManager` now computes per-session capabilities based on:
  - audio bridge availability,
  - Gemini audio input flag,
  - Gemini native audio output flag,
  - ElevenLabs configuration.
- Audio transport is queue-based with bounded buffers:
  - inbound queue (bridge -> model),
  - outbound queue (model/TTS -> bridge),
  - drop accounting for backpressure.
- Steering path remains active in all modes, including `text_only`.
- Session cleanup now cancels:
  - bridge reader task,
  - pending TTS tasks,
  - background loop,
  - then closes Gemini + bridge.

## Paths

### 1) Text-only direct
- Condition: no usable audio bridge or audio flags disabled.
- Behavior:
  - transcript + steering work,
  - no audio injected to model,
  - no assistant playback to caller.

### 2) Audio direct (Gemini native)
- Condition: bridge ready + `DIRECT_VOICE_STRATEGY=gemini_primary` + `GEMINI_AUDIO_OUTPUT_ENABLED=true`.
- Behavior:
  - inbound customer audio sent to Gemini,
  - Gemini audio callback enqueued and played to bridge.

### 3) TTS primary / fallback path
- Condition:
  - `DIRECT_VOICE_STRATEGY=tts_primary`, or
  - `DIRECT_VOICE_STRATEGY=gemini_primary` with explicit fallback activation.
- Behavior:
  - assistant text from Gemini,
  - TTS streaming chunks from ElevenLabs,
  - chunks enqueued and played to bridge.

## Latency Instrumentation
Per-session metrics include:
- inbound audio queue latency
- model response latency (inbound send -> first assistant output signal)
- TTS latency (assistant text -> first TTS chunk)
- outbound playback latency (queue enqueue -> bridge playback)

Metrics are available:
- in-memory per-session telemetry (`get_session_metrics`) for debugging;
- process-level Prometheus export via `GET /metrics` for external scrape.

## Transfer Interaction
- On warm transfer bridge confirmation, `MangoTransferEngine` attempts to suspend
  direct AI audio via `DirectSessionManager.suspend_audio`.
- This prevents continued AI playback after manager takeover when session mapping exists.
- Bridge stream closure (caller hangup path) now triggers session termination and resource cleanup automatically.

## Limitations
1. Real RTP/ESL transport is implemented in `esl_rtp` mode, but live validation remains required.
2. Metrics export is implemented via Prometheus endpoint, but production dashboards/alerts and SLO thresholds are not yet configured.
3. Text-only sessions are not voice-ready and must not be treated as such.

## Production Blockers (Updated)
1. ESL/RTP baseline exists (`esl_rtp` mode), but not production-validated in live Mango tenant.
2. No live E2E validation on real route: `customer speech -> Gemini -> PSTN playback via Mango trunk`.
3. External scrape endpoint exists, but there are no validated production dashboards/alerts yet.
4. Provider constraints remain: without validated FreeSWITCH RTP path, Direct voice runtime cannot be considered real voice-ready.

## Exit Criteria For These Blockers
1. Validate implemented ESL + RTP ingest/inject path on live tenant contour.
2. Run and document live smoke on Mango tenant with real phone legs and recorded evidence.
3. Configure production dashboards and alert rules (latency, drop-rate, hangup anomalies) on top of exported metrics.
4. Verify interruption, hangup propagation, and transfer handoff in live calls under load.

## Readiness
- `PRODUCTION_READY`: no (no real end-to-end FreeSWITCH RTP validation yet).
- `INTEGRATION_READY`: session capabilities, real bridge loop wiring, fallback orchestration, cleanup.
- `NEEDS_REAL_WORLD_VALIDATION`: real RTP quality/latency and interruption behavior.
- `MOCK_ONLY`: media gateway mock mode.

## Manual Smoke Checklist
1. Customer speaks -> backend receives non-zero inbound chunks.
2. AI responds audibly back to customer.
3. Steering works during active audio session.
4. `/stop` terminates call and closes session resources.
5. Warm transfer bridge suppresses AI audio loop and hands off to manager.

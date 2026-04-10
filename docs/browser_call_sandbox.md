# Browser Call Sandbox

Internal QA tool for talking to the Direct voice agent from a browser without
Mango, FreeSWITCH, or a real phone number.

Status:
- `INTEGRATION_READY` for backend lifecycle and UI wiring
- `NEEDS_REAL_WORLD_VALIDATION` for real microphone/browser audio behavior

It is not a production calling route.

## Purpose

Use this sandbox to validate:

- Direct session startup
- current voice strategy
- transcript persistence
- TTS or Gemini audio response path
- fail-fast behavior
- cleanup on stop or tab disconnect

without touching Mango routes or PSTN numbers.

## Architecture

Path:

`Browser UI -> WebSocket -> BrowserAudioBridge -> DirectSessionManager -> Gemini / TTS -> BrowserAudioBridge -> Browser speaker`

Important boundaries:

- No Mango control plane
- No FreeSWITCH RTP leg
- Same Direct AI runtime as the real agent path
- Same transcript persistence path via `DirectEventHandler`

## Runtime Model

The browser sandbox uses:

- `CallMode.BROWSER`
- `BrowserDirectEngine`
- `BrowserTelephonyAdapter`
- `BrowserAudioBridge`
- selected `AgentProfile` from the admin panel when provided
- the same backend runtime prompt assembly used by the Direct voice path
- agent `voice_strategy`
- controlled `knowledge_context` assembled from `CompanyProfile` and bound `KnowledgeDocument` entries when an agent is selected

It still persists a `Call` row and `TranscriptEntry` rows, so it can be audited
like a normal live AI session.

## UI

Page:

`/browser-call` inside the separate React admin panel (`admin-panel/`)

Current UI shows:

- agent picker
- Start Test Call button
- Stop Test Call button
- microphone state
- session state
- AI speaking state
- transcript list
- debug block with:
  - selected agent
  - call id
  - session id
  - voice strategy
  - active voice path
  - fallback used
  - websocket connected
  - bridge open
  - last latency samples
  - last error

## Backend API

Create session:

`POST /v1/browser-calls`

Body:

```json
{
  "label": "sandbox",
  "agent_profile_id": "optional-agent-uuid"
}
```

Response includes:

- `call_id`
- `session_id`
- `browser_token`
- `websocket_url`
- `status_url`
- `stop_url`
- `voice_strategy`
- `active_voice_path`

Read session:

`GET /v1/browser-calls/{call_id}`

Stop session:

`POST /v1/browser-calls/{call_id}/stop`

Audio WebSocket:

`GET /v1/browser-calls/{call_id}/ws?token=...`

## Required Config

Minimum:

```bash
GEMINI_API_KEY=...
DIRECT_VOICE_STRATEGY=tts_primary
GEMINI_AUDIO_INPUT_ENABLED=true
DIRECT_INITIAL_GREETING_ENABLED=true
```

For `tts_primary`:

```bash
GEMINI_AUDIO_OUTPUT_ENABLED=false
ELEVENLABS_ENABLED=true
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...
```

For `gemini_primary`:

```bash
GEMINI_AUDIO_OUTPUT_ENABLED=true
DIRECT_VOICE_ALLOW_TTS_FALLBACK=true
```

Do not use `experimental_hybrid` unless you intentionally want mixed-path QA.

## Manual Smoke Checklist

1. Start backend.
2. Open the React admin panel and log in.
3. Open `Browser Call`.
4. Pick an agent if you want to test a specific runtime profile.
5. Click `Start Test Call`.
5. Allow microphone access.
6. Confirm debug shows:
   - `status=IN_PROGRESS`
   - non-empty `session_id`
   - expected selected agent
   - expected `voice_strategy`
7. Wait for greeting.
8. Speak one short phrase.
9. Confirm transcript updates.
10. Confirm the browser plays the AI response.
11. Click `Stop Test Call`.
12. Confirm:
   - session leaves `IN_PROGRESS`
   - no hanging UI state
   - transcript remains visible via `GET /v1/browser-calls/{call_id}`

## Disconnect Behavior

Current policy:

- if the browser tab disconnects, the bridge closes
- the Direct session terminates
- the browser session is not resumed automatically

This is intentional for the first QA version:
it is safer to end the session explicitly than to leave a hidden hanging runtime.

## Observability

Watch these logs:

- `browser_telephony.connect`
- `browser_bridge.opened`
- `browser_bridge.client_attached`
- `session_manager.created`
- `session_manager.assistant_reply_started`
- `session_manager.tts_reply_started`
- `session_manager.voice_fallback_activated`
- `session_manager.session_failed`
- `session_manager.terminated`

## Known Limits

- No real PSTN validation
- No browser reconnect/resume flow
- Browser audio behavior still needs manual validation in a real browser
- UI is internal and intentionally simple; backend observability is the priority

## Honest Status

- `PRODUCTION_READY`: no
- `INTEGRATION_READY`: yes
- `NEEDS_REAL_WORLD_VALIDATION`: yes
- `MOCK_ONLY`: no

Reason:
the backend route is real and not a toy mock, but browser microphone/speaker
behavior still requires manual verification in an actual browser session.

# Browser Audio Debug

This document records the current browser audio debug chain for the admin-panel
`Browser Call` screen and the concrete failures that were found while tracing
browser capture, transport, runtime, TTS, and playback.

Status:

- `INTEGRATION_READY`: yes
- `NEEDS_REAL_WORLD_VALIDATION`: yes
- `PRODUCTION_READY`: no

Reason:

- browser-side diagnostics are now built into the real Browser Call screen
- backend debug endpoints can isolate outbound audio without using the microphone
- automated tests confirm loopback/test-tone/debug wiring and cleanup paths
- audible greeting and audible AI reply are still not proven by a real manual
  browser run with live provider credentials

## Audio Chain

Main QA path:

`Browser mic -> browser websocket -> BrowserAudioBridge -> DirectSessionManager -> Gemini / TTS -> BrowserAudioBridge -> browser websocket -> browser playback`

Additional debug paths:

1. `Local mic loopback`
   - browser mic -> browser playback
   - does not use backend
   - isolates microphone / AudioContext / local playback policy issues

2. `Backend test tone`
   - backend generates 440Hz PCM
   - backend sends audio through the same browser websocket outbound path
   - isolates backend -> browser transport and browser playback

3. `Backend TTS playback`
   - backend uses the active session voice provider
   - audio is sent back through the same browser outbound path
  - isolates TTS + outbound audio path

4. `Hardcoded local playback`
   - browser-generated beep
   - no backend
   - isolates browser playback pipeline itself

## Concrete Problems Found

### 1. React cleanup could stop active browser audio on normal re-render

This was the most serious browser-path bug found.

The page cleanup used a callback whose identity changed when session/debug state
changed, so React could run teardown while replacing the effect during a normal
re-render.

Practical effect:

- active microphone capture could stop
- playback path could die
- UI could still briefly look active

This was a real silence-class bug, not a hypothetical one.

### 2. Browser media startup happened too late

`getUserMedia()` and `AudioContext` startup happened only after async backend
startup work had already begun.

That is risky because modern browsers can require a live user gesture for media
startup and `AudioContext.resume()`.

Practical effect:

- suspended `AudioContext`
- capture not really starting
- playback blocked until another interaction

### 3. Browser-side observability was too weak

Before this debug pass, there was not enough information in the UI to say
whether failure was in:

- mic capture
- websocket transport
- runtime/model
- TTS
- playback

That made “тишина” hard to localize.

### 4. TTS debug could be falsely green with stub voice

`StubVoiceProvider` returns silence.

If Browser Call exposed a TTS test without detecting stub voice, the UI could
report a “successful” TTS playback that was actually silent.

That would be a false-positive QA result, so it is now blocked explicitly.

### 5. Audio could arrive but remain unprovable as human-audible

Before this pass, there was no artifact to distinguish:

- audio never arrived
- audio arrived but playback failed
- audio arrived and playback scheduled correctly, but operator still heard silence

Now the Browser Call screen exposes:

- WAV dump of the last received audio
- waveform canvas
- playback node lifecycle logs

This does not magically prove audibility by itself, but it removes the previous
"we have no artifact" blind spot.

## Fixes Applied

### Browser Call UI

In `admin-panel/src/pages/BrowserCallPage.tsx`:

- browser audio runtime is prepared before backend browser session creation
- `AudioContext.resume()` is called explicitly before capture and playback
- re-render teardown bug is fixed via stable unmount refs
- local debug counters are exposed:
  - `mic_chunks_count`
  - `ws_connected`
  - `audio_context_state`
  - `outbound_chunks_count`
  - `inbound_chunks_count`
  - `playback_start_count`
  - `last_playback_error`
  - `last_transport_error`
- sample-rate/channel-mode details are surfaced:
  - browser input sample rate
  - target sample rate (`16000`)
  - mono downmix path
- fail-fast browser-side audio health state is surfaced:
  - `NO_AUDIO_IN`
  - `NO_AUDIO_OUT`
- new debug actions are available directly in the Browser Call screen:
  - `Test Mic Loopback`
  - `Play Test Tone from Backend`
  - `Test TTS`
  - `Play Hardcoded Audio`
  - `Download last audio`
- inbound audio is buffered in memory and exported as WAV
- inbound audio waveform is rendered on canvas
- playback pipeline logs now include:
  - node created
  - playback started
  - playback ended
  - gain value
  - sample-rate mismatch info

### Backend

In `app/api/v1/browser_calls.py`:

- added `POST /v1/browser-calls/{call_id}/debug/test-tone`
- added `POST /v1/browser-calls/{call_id}/debug/test-tts`
- test tone generates synthetic PCM16 sine wave at `440Hz`
- TTS debug uses the real active session voice provider
- TTS debug explicitly fails when the session is using `StubVoiceProvider`

In `app/integrations/browser/audio_bridge.py`:

- browser bridge logs include:
  - `session_id`
  - `agent_id`
  - `voice_strategy`
  - `active_voice_path`
- sampled inbound/outbound audio logging is present for browser sessions

## What Is Verified

Verified by automated tests:

- Browser Call page renders inside the admin panel
- browser audio runtime starts
- `AudioContext.resume()` is called
- microphone PCM is sent into websocket transport
- inbound binary audio triggers browser playback scheduling
- microphone denial does not create a backend session
- websocket transport failure stops the backend session
- local loopback starts without creating a backend session
- backend test tone endpoint is wired and callable
- backend TTS endpoint is wired and explicitly rejects stub voice
- browser generates WAV dump from inbound audio
- waveform rendering path is wired
- hardcoded local playback path is wired
- backend browser session create / stop / disconnect cleanup remains green

Relevant runs:

- `python3 -m pytest -q tests/test_admin_auth.py tests/test_browser_call_sandbox.py`
- `cd admin-panel && npm test -- browser-call.smoke.test.tsx`
- `cd admin-panel && npm run build`

## What Is Not Yet Proven

Still not proven live:

- that a human operator hears the local loopback in a real browser
- that a human operator hears the backend test tone in a real browser
- that a human operator hears TTS playback in a real browser
- that a human operator hears hardcoded local playback in a real browser
- that greeting is audible in a real browser conversation
- that AI reply is audible in a real browser conversation
- that transcript visibly updates during a real spoken interaction

This matters:

- automated tests confirm the wiring
- they do not prove human-audible output on the current machine/browser

## Browser Policy Notes

Current implementation assumes browser audio APIs are user-gesture sensitive.

The Browser Call screen now explicitly resumes `AudioContext` during button
click-driven startup to align with that constraint.

Practical debug fields exposed for browser-policy issues:

- `audio context`
- `browser mic permission`
- `input sample rate`
- `target sample rate`
- `channel mode`
- `browser playback error`
- `playback nodes created`
- `playback gain`
- `playback mismatch`
- `playback diagnostic`

Current browser assumptions:

- browser capture can start at a hardware sample rate such as `48000 Hz`
- outbound PCM target remains `16000 Hz mono 16-bit`
- the browser playback path resamples the received PCM buffer through Web Audio

These are implemented and observable in the debug panel, but still need live QA
in the target browser.

## Manual Verification Checklist

### 1. Browser-only loopback

1. Open `Browser Call`
2. Click `Test Mic Loopback`
3. Allow microphone access
4. Confirm:
   - `loopback active=yes`
   - `loopback mic chunks` increases
   - `loopback playback chunks` increases
5. Speak briefly
6. Confirm whether your own voice is audible
7. Click `Stop Loopback`

Interpretation:

- if loopback is not audible, stop here: the problem is browser capture,
  browser playback, or local browser media policy

### 2. Backend tone

1. Start a Browser Call session
2. Confirm:
   - `browser websocket=connected`
   - `audio context=running`
3. Click `Play Test Tone from Backend`
4. Check:
   - `browser inbound audio chunks` increases
   - `browser playback starts` increases
5. Confirm whether a tone is audible

Interpretation:

- if tone is not audible but loopback is audible, the likely problem is backend
  transport or browser playback of inbound websocket audio

Additional artifact:

- use `Download last audio`
- if the WAV is non-empty and the waveform is visible, upstream audio reached
  the browser

### 3. Backend TTS

1. Start a Browser Call session with real ElevenLabs configured
2. Click `Test TTS`
3. Check:
   - inbound audio chunk count increases
   - playback start count increases
4. Confirm whether TTS is audible

Interpretation:

- if tone works but TTS does not, the likely problem is TTS provider or TTS
  output quality/shape

### 3b. Hardcoded local playback

1. Without using backend audio, click `Play Hardcoded Audio`
2. Confirm whether a local beep is audible
3. Check debug:
   - `playback nodes created`
   - `browser playback starts`
   - `playback gain=1`
   - `playback mismatch`

Interpretation:

- if hardcoded local playback is not audible, the problem is browser playback,
  device output selection, browser policy, or local output routing

### 4. Full conversation

1. Start Browser Call
2. Allow mic
3. Wait for greeting
4. Say a short phrase
5. Confirm:
   - transcript appears
   - outbound chunks increase
   - inbound chunks increase
   - playback starts increase
   - AI reply is audible
6. Click `Stop Test Call`
7. Start again
8. Close the tab during an active session and verify cleanup

## Honest Current Conclusion

After this debug pass:

- browser audio failures are more observable
- local/browser-only capture and playback can be isolated from backend/runtime
- backend outbound audio can be isolated from Gemini/TTS
- TTS debug no longer produces false-positive success with stub voice
- inbound browser audio now leaves a downloadable WAV artifact
- inbound browser audio now leaves a visible waveform artifact

But the final product question is still unresolved until a real operator run is
performed:

- local loopback audible: not yet proven here
- backend tone audible: not yet proven here
- TTS audible: not yet proven here
- greeting audible: not yet proven here
- AI reply audible: not yet proven here

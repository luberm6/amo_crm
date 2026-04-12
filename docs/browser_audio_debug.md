# Browser Audio Debug

This document records the current browser audio debug chain for the admin-panel
`Browser Call` screen and the specific ElevenLabs audio-quality defect that was
found in the Direct runtime / browser sandbox path.

Status:

- `INTEGRATION_READY`: yes
- `NEEDS_REAL_WORLD_VALIDATION`: yes
- `PRODUCTION_READY`: no

Reason:

- the ElevenLabs request path is now confirmed alive
- the browser playback path is now instrumented enough to localize format bugs
- the main corruption bug was in PCM16 chunk boundary handling, not in auth or
  provider selection
- automated tests prove the new PCM reassembly contract
- human audibility in a live browser still needs manual listening on a real
  machine

## Canonical Audio Contract

The explicit contract for the ElevenLabs browser path is now:

1. ElevenLabs response
   - encoding: `pcm_s16le`
   - sample rate: `16000`
   - channels: `1`
   - container: `raw`
   - endian: `little`

2. Backend internal TTS representation
   - raw PCM16 little-endian mono at `16000Hz`
   - odd HTTP chunk boundaries are reassembled before dispatch
   - no mid-stream zero padding is allowed

3. Browser bridge payload
   - raw PCM16 little-endian mono at `16000Hz`
   - websocket binary frames may be fragmented arbitrarily
   - frontend reassembles odd websocket boundaries before decoding

4. Frontend playback
   - raw PCM16 -> float32 conversion
   - resample from `16000Hz` to `AudioContext.sampleRate` when needed
   - playback through `AudioBufferSourceNode`

## Root Cause That Was Found

The main noise bug was not a provider outage and not an API-key issue.

The concrete defect was:

- ElevenLabs streaming regularly returned odd-sized byte chunks
- the backend session path padded each odd chunk immediately with `b"\\x00"`
- that inserted synthetic bytes into the middle of a PCM16 stream
- once sample alignment drifted, the browser still received audio, but the
  decoded waveform became badly corrupted and sounded like speech through harsh
  digital noise

The old behavior was effectively:

`odd chunk -> append zero byte now -> enqueue corrupted PCM samples`

The corrected behavior is now:

`odd chunk -> hold carry byte -> merge with next chunk -> emit aligned PCM16`

Only the final dangling byte at end-of-stream may be padded, and that is logged
explicitly.

## Evidence

### Real ElevenLabs stream inspection

During a live local probe against ElevenLabs streaming, the provider returned
many odd-sized HTTP chunks. Example observations:

- raw streaming response contained many odd chunks such as `2407`, `1711`,
  `1309`, `1`, `1491`, `1671`
- one real stream logged `odd_chunks=106`
- the aligned output after the new reassembler was:
  - `aligned_byte_length=150094`
  - `sample_rate=16000`
  - `channels=1`
  - `odd_length=false`

This proves the provider can fragment PCM arbitrarily and that the runtime must
not treat chunk boundaries as sample boundaries.

### Historical corruption reproduction

Before the fix, a controlled reproduction showed:

- raw bytes: `169412`
- old per-chunk padding behavior would have produced: `169584`
- synthetic bytes inserted into the stream: `172`

Those inserted bytes are exactly the kind of corruption that explains “voice is
audible but buried in noise”.

### Automated regression coverage

The following tests now guard this contract:

- `tests/test_audio_utils.py`
  - verifies chunk reassembly preserves PCM16 sample boundaries
  - verifies PCM metadata/RMS reporting
- `tests/test_direct_audio_runtime.py`
  - verifies `DirectSessionManager` preserves PCM16 stream integrity across odd
    chunks
- `admin-panel/src/test/browser-call.smoke.test.tsx`
  - verifies odd-length websocket PCM frames are reassembled before playback

## Instrumentation Added

### Backend

The following logs now exist to localize audio failures:

- `elevenlabs.response_audio_metadata`
- `elevenlabs.audio_bytes_received`
- `session_manager.tts_audio_prepared`
- `browser_bridge.tts_chunk_sent`

These logs include, where applicable:

- `provider`
- `session_id`
- `call_id`
- `voice_strategy`
- `active_voice_path`
- `format`
- `sample_rate`
- `channels`
- `sample_width_bits`
- `container`
- `endian`
- `byte_length`
- `first_bytes_hex`
- `rms`
- `peak`
- `silence_ratio`
- `clipping_ratio`

### Frontend

The browser playback path now logs:

- `browser_call.audio_chunk_received`
- `browser_playback.audio_format_detected`
- `browser_playback.resample_applied`
- `browser_playback.decode_suspicious`
- `browser_playback.output_started`

This makes it possible to distinguish:

- transport fragmentation
- decode path mismatch
- resample activity
- too-quiet playback
- browser-side playback scheduling

## Debug Artifacts

Two opt-in env vars are available for local diagnosis:

```bash
AUDIO_DEBUG_DUMP_ENABLED=true
AUDIO_DEBUG_DUMP_DIR=/tmp/amo_crm_audio_debug
```

When enabled, the runtime may write WAV artifacts such as:

- `raw_elevenlabs_response.wav`
- `raw_elevenlabs_stream.wav`
- `backend_prepared_tts.wav`
- `browser_bridge_outgoing_tts.wav`

These are intentionally debug-only artifacts and are not required for normal
runtime operation.

## Controlled Experiments Completed

1. Real ElevenLabs streaming audit
   - result: provider returned many odd chunk sizes
   - conclusion: chunk boundary corruption was a credible primary failure mode

2. Historical corruption replay
   - result: old logic would inject zero bytes mid-stream
   - conclusion: backend conversion was corrupting PCM16 payloads

3. Browser odd-frame playback regression
   - result: frontend now reassembles odd websocket payloads before playback
   - conclusion: browser decode path no longer trusts websocket frame alignment

4. Browser synthetic playback smoke
   - result: hardcoded/local playback path and backend test playback wiring are
     still green in automated smoke coverage
   - conclusion: the core browser playback path remains intact after the fix

## What Is Verified

Verified:

- ElevenLabs TTS request/response path returns audio bytes
- ElevenLabs streaming can fragment PCM into odd byte lengths
- backend now reassembles odd PCM16 chunk boundaries instead of corrupting them
- browser playback now reassembles odd websocket PCM boundaries as well
- browser smoke tests still exercise playback scheduling after the fix

## What Is Not Yet Proven

Still not proven by this document alone:

- that a human operator hears fully clean ElevenLabs speech in a live browser
- that every browser/device combination behaves identically
- that no downstream artifact remains outside the tested local/browser setup

That means:

- the primary corruption bug is fixed at the format-contract level
- live manual listening is still required before claiming fully verified audio
  quality

## Manual Verification Checklist

1. Set:

```bash
ELEVENLABS_ENABLED=true
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...
DIRECT_VOICE_STRATEGY=tts_primary
```

2. Optionally enable debug dumps:

```bash
AUDIO_DEBUG_DUMP_ENABLED=true
AUDIO_DEBUG_DUMP_DIR=/tmp/amo_crm_audio_debug
```

3. Start backend and admin panel.

4. In `Browser Call`:
   - select an agent
   - start a test call
   - run `Test TTS`
   - confirm voice is audible and no longer dominated by digital noise

5. If quality is still bad:
   - inspect browser console for `browser_playback.*` logs
   - inspect backend logs for `elevenlabs.*` and
     `session_manager.tts_audio_prepared`
   - compare generated WAV artifacts between raw provider output and prepared
     backend output

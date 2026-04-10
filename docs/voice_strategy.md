# Voice Strategy

This document defines the explicit Direct voice strategy model.

## Modes

### `disabled`
- Direct voice calls must not start.
- Use this as the safe default until a real voice path is configured.

### `gemini_primary`
- `PRIMARY`: Gemini native audio
- `FALLBACK`: Gemini text + ElevenLabs TTS, only when:
  - `DIRECT_VOICE_ALLOW_TTS_FALLBACK=true`
  - ElevenLabs is fully configured
- Initial greeting uses the Gemini primary path.
- Recommended for long-term production target.
- Not yet live-validated on a real PSTN contour.

### `tts_primary`
- `PRIMARY`: Gemini text + ElevenLabs TTS
- `FALLBACK`: none
- Gemini native audio must not be active here.
- Initial greeting uses the TTS primary path.
- Recommended for the first live call when a deterministic first-turn is more important than the lowest possible latency.

### `experimental_hybrid`
- Mixed mode for controlled experiments only.
- Requires both Gemini native audio and ElevenLabs.
- Current behavior:
  - initial greeting uses TTS
  - ongoing dialog prefers Gemini native audio
  - TTS fallback remains available
- Do not use as the default production mode.

## Required Config

### Common Direct voice
```bash
DIRECT_VOICE_STRATEGY=...
GEMINI_API_KEY=...
GEMINI_AUDIO_INPUT_ENABLED=true
MEDIA_GATEWAY_ENABLED=true
MEDIA_GATEWAY_PROVIDER=freeswitch
MEDIA_GATEWAY_MODE=esl_rtp
```

### `gemini_primary`
```bash
DIRECT_VOICE_STRATEGY=gemini_primary
GEMINI_AUDIO_OUTPUT_ENABLED=true
DIRECT_VOICE_ALLOW_TTS_FALLBACK=true
```

Optional explicit fallback:
```bash
ELEVENLABS_ENABLED=true
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...
```

### `tts_primary`
```bash
DIRECT_VOICE_STRATEGY=tts_primary
GEMINI_AUDIO_OUTPUT_ENABLED=false
ELEVENLABS_ENABLED=true
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...
```

### `experimental_hybrid`
```bash
DIRECT_VOICE_STRATEGY=experimental_hybrid
GEMINI_AUDIO_OUTPUT_ENABLED=true
ELEVENLABS_ENABLED=true
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...
```

## What Is Explicitly Forbidden

- `tts_primary` with `GEMINI_AUDIO_OUTPUT_ENABLED=true`
- starting Direct voice with `DIRECT_VOICE_STRATEGY=disabled`
- relying on “both configured, runtime will decide somehow”

## Recommended Usage

### First live call
- `tts_primary`
- reason: more deterministic initial greeting path

### Production target
- `gemini_primary`
- optional explicit ElevenLabs fallback

### Experimental testing
- `experimental_hybrid`
- only with clear logging and operator awareness

## Observability

Each Direct session should expose:
- `voice_strategy`
- `primary_voice_path`
- `fallback_voice_path`
- `active_voice_path`
- `initial_greeting_path`

Logs and telemetry should show whether a reply started through:
- `gemini_native`
- `tts_primary`
- `tts_fallback`

## Honest Status

- Strategy enforcement is implemented in config/runtime/preflight/startup validation.
- Real live-call validation is still required before calling any mode production-ready.

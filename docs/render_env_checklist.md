# Render Env Checklist

## Required For Backend Boot

- `BACKEND_URL=https://<your-backend>.onrender.com`
- `ADMIN_EMAIL=admin@example.com`
- `ADMIN_PASSWORD=CHANGE_ME`
- `ADMIN_AUTH_SECRET=CHANGE_ME`
- `PROVIDER_SETTINGS_SECRET=CHANGE_ME`
- `ADMIN_CORS_ORIGINS=https://<your-admin>.onrender.com`

## Required For Browser Voice

- `GEMINI_API_KEY=...`
- `ELEVENLABS_API_KEY=...`
- `ELEVENLABS_VOICE_ID=...`

## Recommended Browser Sandbox Runtime Flags

- `TELEPHONY_PROVIDER=stub`
- `DIRECT_VOICE_STRATEGY=tts_primary`
- `DIRECT_VOICE_ALLOW_TTS_FALLBACK=true`
- `GEMINI_AUDIO_INPUT_ENABLED=true`
- `GEMINI_AUDIO_OUTPUT_ENABLED=false`
- `DIRECT_INITIAL_GREETING_ENABLED=true`
- `ELEVENLABS_ENABLED=true`

## Optional For PSTN / Mango Later

- `MANGO_API_KEY=...`
- `MANGO_API_SALT=...`
- `MANGO_FROM_EXT=...`
- `MANGO_WEBHOOK_SHARED_SECRET=...`
- `MEDIA_GATEWAY_ENABLED=true`
- `MEDIA_GATEWAY_MODE=esl_rtp`
- `FREESWITCH_ESL_HOST=...`
- `FREESWITCH_ESL_PASSWORD=...`

## Frontend Static Site Env

- `VITE_API_BASE_URL=https://<your-backend>.onrender.com`

## Important

- Render-managed `DATABASE_URL` and `REDIS_URL` come from the blueprint
- provider settings do not enable Mango routing automatically
- browser voice needs only Gemini + ElevenLabs, not Mango

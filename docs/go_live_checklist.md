# Go-Live Checklist (Mango + FreeSWITCH + Direct AI Voice)

## Readiness Classification
- `code-complete`: Mango control-plane, FreeSWITCH control/media baseline, Direct audio loop, transfer orchestration.
- `infra-dependent`: Mango trunk routing, FreeSWITCH SIP/ESL/RTP config, public callbacks, NAT/firewall/SRTP.
- `реально проверено`: automated tests + integration-like simulations.
- `ещё блокируется`: live E2E call validation on real Mango tenant contour.

## 1) Infrastructure
1. PostgreSQL is reachable from backend.
2. Redis is reachable from backend and workers.
3. FreeSWITCH is running with:
- `mod_event_socket` enabled.
- SIP profile for Mango trunk.
- RTP ports open in firewall.
4. Public HTTPS URL exists for backend webhooks.

## 2) Backend Environment
Set and verify:
- `ENVIRONMENT=production`
- `LOG_FORMAT=json`
- `DATABASE_URL`
- `REDIS_URL`
- `API_KEY`

Direct / Gemini:
- `GEMINI_API_KEY`
- `GEMINI_MODEL_ID`
- `GEMINI_AUDIO_INPUT_ENABLED=true`
- `GEMINI_AUDIO_OUTPUT_ENABLED=true` (native audio) or configure ElevenLabs fallback.

ElevenLabs fallback:
- `ELEVENLABS_ENABLED=true`
- `ELEVENLABS_API_KEY`
- `ELEVENLABS_VOICE_ID`

Mango:
- `MANGO_API_KEY`
- `MANGO_API_SALT`
- `MANGO_FROM_EXT`
- webhook guard: `MANGO_WEBHOOK_SECRET` and/or `MANGO_WEBHOOK_SHARED_SECRET`

Media gateway:
- `MEDIA_GATEWAY_ENABLED=true`
- `MEDIA_GATEWAY_PROVIDER=freeswitch`
- `MEDIA_GATEWAY_MODE=esl_rtp`
- `FREESWITCH_ESL_HOST`
- `FREESWITCH_ESL_PORT`
- `FREESWITCH_ESL_PASSWORD`
- `FREESWITCH_RTP_IP`
- `FREESWITCH_RTP_PORT_START`
- `FREESWITCH_RTP_PORT_END`
- optional codec knobs:
  - `FREESWITCH_RTP_INBOUND_CODEC`
  - `FREESWITCH_RTP_OUTBOUND_CODEC`

## 3) Database / Migrations
1. Run `alembic upgrade head`.
2. Confirm latest revisions applied, including manager cooldown durability (`available_after` migration).

## 4) FreeSWITCH Integration
1. ESL auth works from backend host.
2. Attach command template matches your FS deployment:
- `FREESWITCH_ATTACH_COMMAND_TEMPLATE`
3. Hangup command template matches deployment:
- `FREESWITCH_HANGUP_COMMAND_TEMPLATE`
4. Event subscription includes required events:
- `CHANNEL_CREATE`, `CHANNEL_ANSWER`, `CHANNEL_HANGUP(_COMPLETE)`, `PLAYBACK_*`, `CHANNEL_BRIDGE`.

## 5) Mango Integration
1. Mango callback API credentials valid.
2. Mango webhook points to:
- `POST /v1/webhooks/mango`
3. Mango trunk routes call leg into FreeSWITCH (DID/extension/context mapping).

## 6) Runtime Services
1. Backend API running.
2. Worker running:
- `celery -A app.workers.celery_app worker --loglevel=info -Q default`
3. Beat running:
- `celery -A app.workers.celery_app beat --loglevel=info`

## 7) Preflight Checks
1. `GET /health` returns `ok`.
2. `GET /ready` returns `ok` for DB and Redis.
3. `GET /metrics` responds (if metrics enabled).
4. Logs show FreeSWITCH ESL connected after first attach.

## 8) Hard Blockers Before Calling It Production
1. No live signed-off E2E call evidence on real Mango tenant.
2. No confirmed production-quality network profile (latency/jitter/NAT/SRTP).
3. No finalized alert thresholds/runbooks for voice quality degradation.

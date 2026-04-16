# Production Readiness

Status as of 2026-04-03. Updated after each stabilisation sprint.

---

## Readiness Matrix

| Component | Status | Blocker / Note |
|-----------|--------|----------------|
| Vapi call engine | PRODUCTION_READY | Used in pilot; battle-tested |
| Vapi webhook processing | PRODUCTION_READY | Signature validation, idempotency guard |
| Transfer service (state machine) | PRODUCTION_READY | SELECT FOR UPDATE on Postgres; SQLite tests only cover state logic |
| Rate limiting (Redis) | PRODUCTION_READY | Redis + DB dual-layer; fail-open by design |
| Session coordinator (Redis) | INTEGRATION_READY | Lua scripts validated; never run against real concurrent load |
| Direct / Gemini Live engine | INTEGRATION_READY | End-to-end session lifecycle complete; needs real Mango audio |
| Mango control plane (outbound call) | INTEGRATION_READY | MangoTelephonyAdapter implemented; awaits credential provisioning |
| Mango media / audio bridge | NEEDS_REAL_WORLD_VALIDATION | FreeSwitchAudioBridge + ESL/RTP path implemented; live contour validation still required |
| ElevenLabs TTS | NEEDS_REAL_WORLD_VALIDATION | ElevenLabsVoiceProvider implemented; not load-tested |
| Summary / Whisper pipeline | PRODUCTION_READY | 18 tests; negation-aware; backward-compat preserved |
| Telegram bot | PRODUCTION_READY | Async, handles all call lifecycle events |
| Database migrations (Alembic) | PRODUCTION_READY | All models tracked; run `alembic upgrade head` before deploy |

Status definitions:
- **PRODUCTION_READY** — tested, deployed, no known blockers
- **INTEGRATION_READY** — implemented and unit-tested; awaits real-world integration
- **NEEDS_REAL_WORLD_VALIDATION** — implemented; behaviour under real load/infra unknown
- **MOCK_ONLY** — feature exists as stub only, no real implementation

---

## Known Production Blockers

1. **Mango audio bridge is implemented but not signed off live** — `FreeSwitchAudioBridge`
   and `esl_rtp` media path exist, but first real Mango + FreeSWITCH + PSTN proof is still pending.

2. **Outbound voice path must be configured explicitly** — use either:
   - `GEMINI_AUDIO_OUTPUT_ENABLED=true` for native Gemini audio, or
   - ElevenLabs (`ELEVENLABS_ENABLED=true`, key, voice ID).
   If neither is configured, Direct calls now fail fast instead of running silently.

3. **SELECT FOR UPDATE Postgres tests** — transfer double-booking protection is
   tested only against SQLite in CI. Run `tests/test_transfer_postgres.py` with
   a real Postgres URL before going to production.
   ```
   TEST_DATABASE_URL=postgresql+asyncpg://... pytest tests/test_transfer_postgres.py -m postgres
   ```

4. **Session coordinator Redis** — `RedisSessionStore` has no integration tests
   against real Redis. All tests use `InMemorySessionStore`. Validate under load
   before enabling `DIRECT_MAX_SESSIONS > 1` in production.

---

## Feature Flags

### Vapi mode
```
VAPI_API_KEY=<key>
VAPI_ASSISTANT_ID=<id>
VAPI_PHONE_NUMBER_ID=<id>
VAPI_SERVER_URL=https://your-domain.com
VAPI_WEBHOOK_SECRET=<secret>
```

### Direct / Gemini Live mode
```
GEMINI_API_KEY=<key>
GEMINI_MODEL_ID=gemini-2.5-flash-native-audio-preview-12-2025
DIRECT_MAX_SESSIONS=10
```

### Mango control plane (outbound calls via Direct mode)
```
TELEPHONY_PROVIDER=mango
MANGO_API_KEY=<key>
MANGO_API_SALT=<salt>
MANGO_FROM_EXT=<extension>
```

### Mango media / SIP audio
```
MEDIA_GATEWAY_ENABLED=true
MEDIA_GATEWAY_PROVIDER=freeswitch
MEDIA_GATEWAY_MODE=esl_rtp
FREESWITCH_ESL_HOST=<host>
FREESWITCH_ESL_PORT=8021
FREESWITCH_ESL_PASSWORD=<password>
FREESWITCH_RTP_IP=<backend_ip>
FREESWITCH_RTP_PORT_START=16384
FREESWITCH_RTP_PORT_END=32768
```
Implemented in code; blocked only on live infrastructure validation.

### ElevenLabs TTS
```
ELEVENLABS_API_KEY=<key>
ELEVENLABS_VOICE_ID=<voice_id>
ELEVENLABS_ENABLED=true
```

---

## Production Checklist

Required before first production deployment:

- [ ] `ENVIRONMENT=production`
- [ ] `DATABASE_URL` — PostgreSQL (not SQLite)
- [ ] `REDIS_URL` — persistent Redis instance
- [ ] `API_KEY` — strong random value (`python -c "import secrets; print(secrets.token_urlsafe(32))"`)
- [ ] `ENFORCE_QUIET_HOURS=true` + calling window configured
- [ ] `RATE_LIMIT_ENABLED=true`
- [ ] `LOG_FORMAT=json`
- [ ] `TELEGRAM_BOT_TOKEN` — real bot
- [ ] `BACKEND_URL` — public HTTPS URL
- [ ] At minimum one real call engine: `VAPI_API_KEY` or `GEMINI_API_KEY`
- [ ] `TELEPHONY_PROVIDER=mango` (when using Direct mode) with Mango credentials
- [ ] Alembic migrations applied: `alembic upgrade head`
- [ ] Postgres transfer tests passing: `pytest tests/test_transfer_postgres.py -m postgres`

---

## Next Steps (Mango Direct Voice Integration)

1. **Provision Mango credentials** — `MANGO_API_KEY`, `MANGO_API_SALT`, `MANGO_FROM_EXT`
2. **Smoke test Mango control plane** — verify `MangoTelephonyAdapter.initiate_call()` places a real outbound call
3. **Validate FreeSWITCH contour** — SIP trunk from Mango → FreeSWITCH + ESL/RTP back to backend
4. **Run first real call runbook** — prove `customer speech -> Gemini -> PSTN playback`
5. **Load test RedisSessionStore** — concurrent sessions under real Redis
6. **Validate ElevenLabs or native Gemini audio** — latency and quality on live contour

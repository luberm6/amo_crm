# First Call Runbook (Mango -> FreeSWITCH -> backend -> Gemini -> voice -> caller)

Goal:
`Mango -> FreeSWITCH -> backend -> Gemini/ElevenLabs -> FreeSWITCH -> customer`

This run is successful only if:
- the callee hears the initial greeting immediately after answer;
- caller speech reaches Gemini;
- AI audio is heard back by the caller;
- `/stop` completes cleanly.

If any of these fail, the E2E attempt is not complete.

## 1) Configure backend
Minimum Direct voice config:
```bash
ENVIRONMENT=production
TELEPHONY_PROVIDER=mango
MEDIA_GATEWAY_ENABLED=true
MEDIA_GATEWAY_PROVIDER=freeswitch
MEDIA_GATEWAY_MODE=esl_rtp
MANGO_API_KEY=...
MANGO_API_SALT=...
MANGO_FROM_EXT=...
MANGO_WEBHOOK_SHARED_SECRET=...
GEMINI_API_KEY=...
DIRECT_VOICE_STRATEGY=tts_primary
DIRECT_VOICE_ALLOW_TTS_FALLBACK=true
GEMINI_AUDIO_INPUT_ENABLED=true
DIRECT_INITIAL_GREETING_ENABLED=true
DIRECT_INITIAL_GREETING_TEXT=Здравствуйте! Это AI-ассистент. Чем могу помочь?
```

Choose one explicit voice strategy:
```bash
# Option A: primary = Gemini native audio
DIRECT_VOICE_STRATEGY=gemini_primary
GEMINI_AUDIO_OUTPUT_ENABLED=true
```
or
```bash
# Option B: primary = Gemini text + ElevenLabs TTS
DIRECT_VOICE_STRATEGY=tts_primary
GEMINI_AUDIO_OUTPUT_ENABLED=false
ELEVENLABS_ENABLED=true
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...
```
Do not use mixed Gemini + ElevenLabs behavior unless `DIRECT_VOICE_STRATEGY=experimental_hybrid`.

## 2) Bring up stack
1. Start Postgres + Redis.
2. Run migrations:
```bash
alembic upgrade head
```
3. Start backend:
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
4. Start worker + beat:
```bash
celery -A app.workers.celery_app worker --loglevel=info -Q default
celery -A app.workers.celery_app beat --loglevel=info
```
5. Ensure FreeSWITCH is running and reachable by backend ESL.

## 3) Validate control endpoints
1. `GET /health`
2. `GET /ready`
3. Run Direct voice preflight before the first live call:
```bash
python3 scripts/direct_voice_preflight.py
```
4. Or use the API form:
```bash
curl http://<backend>/v1/preflight/direct-voice \
  -H "X-API-Key: <api-key>"
```
5. Do not proceed to a real call while preflight returns `status=fail`.
6. `POST /v1/webhooks/mango` guard test (wrong secret should fail).

## 4) Validate Mango routing
1. In Mango PBX, confirm outbound callback credentials are active.
2. Confirm the source extension/line in `MANGO_FROM_EXT` is valid for callback origination.
3. Confirm SIP routing/trunk sends the AI media leg to FreeSWITCH.
4. Confirm Mango webhook URL points to backend public URL `/v1/webhooks/mango`.
5. Confirm only the dedicated AI test route/number is pointed at this contour.

## 5) Validate FreeSWITCH before calling
1. ESL auth must work from backend host.
2. Confirm attach template matches the deployment:
- `FREESWITCH_ATTACH_COMMAND_TEMPLATE`
3. Confirm RTP ports are open both ways.
4. Confirm backend logs receive `CHANNEL_CREATE` / `CHANNEL_ANSWER` for a test leg.

## 6) Create first direct call
1. Create call in `mode=direct`:
```bash
curl -X POST http://<backend>/v1/calls \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <api-key>" \
  -d '{"phone":"+<customer_phone>","mode":"direct"}'
```
2. Capture `call_id`.

## 7) Confirm answer path
1. Verify logs:
- Mango leg originated.
- FreeSWITCH session attached.
- `channel_answer` observed.
2. Confirm backend call state moved to `IN_PROGRESS`.
3. Confirm no explicit fail-fast logs:
- `session_manager.audio_bridge_attach_failed`
- `session_manager.audio_out_unavailable`
- `call_create_failed`

## 8) Confirm first audible reply
1. The callee should hear the initial greeting immediately after answer.
2. Verify logs:
- `session_manager.initial_greeting_started`
- `freeswitch_gateway.remote_endpoint_primed` or `freeswitch_gateway.audio_buffer_flushed`
3. If the call answers but the greeting is silent, the E2E attempt failed.

## 9) Confirm transcript path
1. Fetch call:
```bash
curl http://<backend>/v1/calls/<call_id>
```
2. Ensure transcript entries appear after customer speech / assistant response.

## 10) Confirm live AI voice reply
1. Speak from customer side.
2. Verify inbound audio counters increase.
3. Verify model response latency appears in metrics/session stats.
4. Verify caller hears assistant:
- native Gemini audio if enabled;
- otherwise ElevenLabs fallback TTS.
5. If backend sees caller speech but the caller hears nothing back, the RTP return path is still broken.

## 11) Stop path validation
1. Call:
```bash
curl -X POST http://<backend>/v1/calls/<call_id>/stop \
  -H "X-API-Key: <api-key>"
```
2. Confirm:
- session terminated;
- bridge closed;
- no orphan audio loop tasks.

## 12) Transfer path validation
1. Trigger transfer:
```bash
curl -X POST http://<backend>/v1/calls/<call_id>/transfer \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <api-key>" \
  -d '{}'
```
2. Confirm:
- manager leg dialed;
- answer confirmed;
- whisper lifecycle completed;
- bridge confirmed;
- AI audio loop suspended after manager takeover.

## 13) Success criteria for first real run
1. Call rings and answers.
2. Caller hears the initial greeting immediately after answer.
3. FreeSWITCH channel events are visible in backend logs.
4. Inbound speech reaches backend/Gemini.
5. Assistant audio is audible to caller.
6. `/stop` completes cleanly.

## 14) Honest exit conditions
- If the caller does not hear the initial greeting, the system is not yet E2E ready.
- If backend receives audio but the caller hears no AI reply, the RTP return path is still broken.
- If any media/model failure leaves the call hanging in `IN_PROGRESS`, treat it as a bug and inspect the explicit fail-fast logs above.

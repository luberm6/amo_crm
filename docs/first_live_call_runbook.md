# First Live Call Runbook

Purpose:
prove a real audible phone call through
`Mango -> FreeSWITCH -> backend -> Gemini -> ElevenLabs -> FreeSWITCH -> caller`

This runbook is for the first real live phone call, not for local feature testing.

## Success Criteria

Treat the first live call as successful only if all of these are true:

1. the call reaches `ANSWERED`;
2. the callee hears the initial greeting immediately after answer;
3. the caller speaks and backend logs show inbound audio/model activity;
4. the caller hears an AI response back in the phone;
5. `/stop` or hangup ends the session cleanly;
6. no fail-fast error logs appear for media/model/TTS.

If the callee does not hear AI audio even once, the run is failed.

## Recommended First-Live-Call Strategy

Use:

```bash
DIRECT_VOICE_STRATEGY=tts_primary
GEMINI_AUDIO_OUTPUT_ENABLED=false
DIRECT_VOICE_ALLOW_TTS_FALLBACK=true
ELEVENLABS_ENABLED=true
```

Reason:
for the first live call this is the most deterministic first-turn path currently supported by the codebase.

Do not use:

```bash
DIRECT_VOICE_STRATEGY=experimental_hybrid
```

for the first live validation.

## 1. Required Environment

Minimum backend environment:

```bash
ENVIRONMENT=production
BACKEND_URL=https://your-public-backend.example.com
API_KEY=CHANGE_ME

DATABASE_URL=postgresql+asyncpg://amo_user:CHANGE_ME@db-host:5432/amo_crm
REDIS_URL=redis://:CHANGE_ME@redis-host:6379/0

TELEPHONY_PROVIDER=mango
MANGO_API_KEY=CHANGE_ME
MANGO_API_SALT=CHANGE_ME
MANGO_FROM_EXT=CHANGE_ME
MANGO_WEBHOOK_SHARED_SECRET=CHANGE_ME

MEDIA_GATEWAY_ENABLED=true
MEDIA_GATEWAY_PROVIDER=freeswitch
MEDIA_GATEWAY_MODE=esl_rtp
FREESWITCH_ESL_HOST=127.0.0.1
FREESWITCH_ESL_PORT=8021
FREESWITCH_ESL_PASSWORD=ClueCon
FREESWITCH_RTP_IP=PUBLIC_OR_REACHABLE_IP
FREESWITCH_RTP_PORT_START=16384
FREESWITCH_RTP_PORT_END=32768

GEMINI_API_KEY=CHANGE_ME
GEMINI_AUDIO_INPUT_ENABLED=true
GEMINI_AUDIO_OUTPUT_ENABLED=false
GEMINI_MODEL_ID=gemini-2.0-flash-live-001

DIRECT_VOICE_STRATEGY=tts_primary
DIRECT_VOICE_ALLOW_TTS_FALLBACK=true
DIRECT_INITIAL_GREETING_ENABLED=true
DIRECT_INITIAL_GREETING_TEXT=Здравствуйте! Это AI-ассистент. Чем могу помочь?
DIRECT_MODEL_RESPONSE_TIMEOUT_SECONDS=8.0

ELEVENLABS_ENABLED=true
ELEVENLABS_API_KEY=CHANGE_ME
ELEVENLABS_VOICE_ID=CHANGE_ME
```

## 2. Bring Up Backend

1. Run migrations:

```bash
alembic upgrade head
```

2. Start API:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

3. Start Celery worker:

```bash
celery -A app.workers.celery_app worker --loglevel=info -Q default
```

4. Start Celery beat:

```bash
celery -A app.workers.celery_app beat --loglevel=info
```

## 3. Preflight Before the Call

Run:

```bash
python3 scripts/direct_voice_preflight.py
```

Do not attempt the first live call while preflight returns:

- `status=fail`

Expected first-live-call voice result:

- `voice_strategy=strategy configured`
- `strategy=tts_primary`
- no failures for:
  - database
  - gemini_api
  - mango_credentials
  - mango_from_ext
  - media_gateway
  - media_gateway_mode
  - telephony_resolution

## 4. Bring Up FreeSWITCH

Minimum requirements:

1. FreeSWITCH process is running.
2. ESL listens on the configured host and port.
3. SIP profile for the Mango trunk is loaded.
4. RTP UDP ports are open bi-directionally.
5. Dialplan routes the AI call leg to the media bridge expected by backend.
6. FreeSWITCH can see `CHANNEL_CREATE`, `CHANNEL_ANSWER`, and RTP for the call leg.

Basic checks:

```bash
nc -vz 127.0.0.1 8021
```

```bash
fs_cli -x "status"
```

```bash
fs_cli -x "sofia status"
```

```bash
fs_cli -x "show channels"
```

## 5. Prepare Mango

Use exactly one dedicated test number that is not used by amoCRM.

Required:

1. Mango API credentials are active.
2. The line/extension in `MANGO_FROM_EXT` is valid for originate/callback.
3. Mango webhook URL points to:

```text
https://your-public-backend.example.com/v1/webhooks/mango
```

4. Only the dedicated AI test route points at this FreeSWITCH contour.
5. Shared account routes used by amoCRM must remain untouched.

## 6. Verify Logs Before Call Start

Backend:

```bash
uvicorn ... 2>&1 | tee backend.log
```

Worker:

```bash
celery ... 2>&1 | tee worker.log
```

FreeSWITCH:

```bash
fs_cli
```

Watch for:

- Mango webhook events
- `CHANNEL_CREATE`
- `CHANNEL_ANSWER`
- `session_manager.initial_greeting_started`
- `freeswitch_gateway.remote_endpoint_primed`
- `freeswitch_gateway.audio_buffer_flushed`
- `direct_engine.call_initiated`

Treat these as hard failures:

- `session_manager.audio_bridge_attach_failed`
- `session_manager.audio_out_unavailable`
- `session_manager.audio_in_unavailable`
- `session_manager.tts_error`
- `session_manager.session_failed`
- `call_create_failed`

## 7. Execute the First Live Call

Create the call in direct mode:

```bash
curl -X POST http://127.0.0.1:8000/v1/calls \
  -H "Content-Type: application/json" \
  -H "X-API-Key: CHANGE_ME" \
  -d '{"phone":"+<real_phone_number>","mode":"direct"}'
```

Capture `call_id`.

## 8. What Must Happen During the Call

### Step A: Answer

Confirm:

1. Mango leg is created.
2. FreeSWITCH channel appears.
3. backend sees `ANSWERED`.

### Step B: Greeting

Confirm:

1. human answers the phone;
2. human hears the greeting immediately;
3. backend logs show greeting started through `tts_primary`.

If answer happened but greeting is silent, stop here and mark the run failed.

### Step C: AI reply

1. human says a short phrase;
2. backend logs show inbound audio and model activity;
3. ElevenLabs logs show synthesized audio;
4. human hears the AI reply in the phone.

If backend sees inbound speech but human hears nothing back, the break is on the return voice path.

### Step D: Stop

Stop the call:

```bash
curl -X POST http://127.0.0.1:8000/v1/calls/<call_id>/stop \
  -H "X-API-Key: CHANGE_ME"
```

Confirm:

1. call terminates;
2. session state leaves `IN_PROGRESS`;
3. bridge closes;
4. no orphan tasks remain in logs.

## 9. RTP and Codec Debug

If there is one-way audio or silence, verify:

1. RTP arrives in FreeSWITCH from Mango.
2. backend receives bridged audio.
3. backend sends RTP back.
4. remote RTP endpoint is detected.
5. the first outbound packet is flushed from buffer.
6. codec expectations match the real leg.

Focus points:

- NAT/firewall on RTP range
- wrong RTP IP advertised by FreeSWITCH
- wrong codec expectation
- ESL connected but attach command not matching deployment
- first outbound audio stuck waiting for remote endpoint

## 10. Failure Isolation Matrix

### Break: Mango -> FreeSWITCH

Signals:

- no channel in FreeSWITCH
- no `CHANNEL_CREATE`

Likely causes:

- wrong Mango route
- wrong SIP trunk/dialplan
- wrong source extension/line

### Break: FreeSWITCH -> backend

Signals:

- FreeSWITCH sees call
- backend does not see attach/audio events

Likely causes:

- ESL unreachable
- wrong attach template
- media gateway disabled

### Break: backend -> Gemini

Signals:

- inbound audio exists
- no model activity

Likely causes:

- missing Gemini key
- websocket/model failure
- timeout

### Break: Gemini -> ElevenLabs

Signals:

- model text activity exists
- no TTS activity

Likely causes:

- wrong `tts_primary` config
- ElevenLabs key/voice invalid
- TTS runtime failure

### Break: TTS -> RTP -> caller

Signals:

- TTS audio generated
- caller hears nothing

Likely causes:

- outbound RTP blocked
- remote endpoint not learned
- wrong RTP IP/ports
- one-way audio / NAT

## 11. What to Record After the Attempt

Record these facts exactly:

1. greeting heard: yes/no
2. AI reply heard: yes/no
3. inbound audio confirmed: yes/no
4. outbound audio confirmed: yes/no
5. full duplex confirmed: yes/no
6. stop confirmed: yes/no
7. one-way audio present: yes/no
8. silence after answer: yes/no
9. average latency impression: low/medium/high
10. exact logs around answer, greeting, first user speech, first AI reply, stop

## 12. Honest Exit Rule

If the person on the phone does not hear AI audio at least once, the result is:

`FAILED`

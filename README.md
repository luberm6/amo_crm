# AMO CRM Voice — AI Sales System

MVP backend for AI-driven outbound voice calls with Telegram control interface.

**Architecture:** Telegram Bot → Backend API → RoutingCallEngine → **Vapi** / **Gemini Live** → Telephony

---

## Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI + uvicorn |
| Database | PostgreSQL 16 + SQLAlchemy 2.x async (asyncpg) |
| Cache / Queue broker | Redis 7 |
| Background tasks | Celery |
| Migrations | Alembic (async) |
| Telegram Bot | aiogram 3.x |
| Config | pydantic-settings (.env) |
| Logging | structlog (JSON prod, console dev) |
| Phone normalization | phonenumbers (Google libphonenumber) |
| WebSocket client | websockets (Direct/Gemini mode) |

---

## Project Structure

```
app/
  api/v1/         REST endpoints
  api/auth.py     API key authentication dependency
  core/           config, logging, exceptions
  db/             engine, session, base ORM
  middleware/     request_id, security_headers
  models/         SQLAlchemy models
  schemas/        Pydantic DTOs
  services/       business logic
  repositories/   DB query layer
  integrations/   call engine abstraction (Vapi, Direct, Stub, Router)
  workers/        Celery app
  main.py         FastAPI app factory
bot/
  handlers/       Telegram command handlers
  main.py         bot entry point
migrations/       Alembic async migrations (0001–0005)
tests/            pytest test suite (170+ tests)
```

---

## Quick Start (Local)

### 1. Clone and install

```bash
cd /path/to/Amo_crm
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — at minimum set TELEGRAM_BOT_TOKEN
```

Или одной командой сразу синхронизировать FreeSWITCH/Mango telephony env локально и в Render:

```bash
make sync-env
```

Что делает команда:
- обновляет локальный `.env`
- обновляет `.env.example`, `.env.local.example`, `.env.production.example`
- merge-safe обновляет telephony env в Render service `amo-crm-api`
- сначала читает текущие env сервиса и сохраняет все остальные ключи, чтобы частичный sync не затирал рабочие секреты

Нужные переменные для этого контура:
- `FREESWITCH_ESL_HOST`
- `FREESWITCH_ESL_PORT`
- `FREESWITCH_ESL_PASSWORD`
- `FREESWITCH_SIP_IP`
- `FREESWITCH_RTP_IP`
- `FREESWITCH_WS_URL`
- `FREESWITCH_WSS_URL`
- `MANGO_SIP_LOGIN`
- `MANGO_SIP_PASSWORD`
- `MANGO_SIP_SERVER`

Если `RENDER_API_KEY` не задан, скрипт попробует взять Render auth из `~/.render/cli.yaml`.
Если и его нет, локальный `.env` всё равно будет обновлён, а Render sync завершится понятной ошибкой.

### 3. Start infrastructure

```bash
docker-compose up -d
# Postgres on 127.0.0.1:5433, Redis on 127.0.0.1:6379
```

### 4. Run migrations

```bash
alembic upgrade head
```

**Migration order:**
1. `0001` — initial schema (calls, managers, audit, steering)
2. `0002` — transcript + vapi_event tables
3. `0003` — warm transfer (transfer_records)
4. `0004` — direct mode (mango_call_id index)
5. `0005` — blocked_phones deny list
6. `0006` — call routing
7. `0007` — transfer hardening (`failure_stage`)
8. `0008` — durable manager cooldown restore (`managers.available_after`)

### 5. Start backend

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

API docs: http://localhost:8000/docs

### 6. Start Telegram bot

```bash
python -m bot.main
```

### 7. (Optional) Celery worker

```bash
celery -A app.workers.celery_app worker --loglevel=info -Q default
```

### 8. Run tests

```bash
pytest -v
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | postgres://... | Async Postgres URL |
| `REDIS_URL` | redis://... | Redis URL |
| `TELEGRAM_BOT_TOKEN` | *(required)* | From @BotFather |
| `BACKEND_URL` | `http://127.0.0.1:8000` | Bot → API URL |
| `ENVIRONMENT` | `development` | `development` / `production` / `testing` |
| `LOG_LEVEL` | `INFO` | Python log level |
| `LOG_FORMAT` | `console` | `console` or `json` (production) |
| `API_KEY` | *(empty = disabled)* | Shared key for bot→backend auth |
| `ENFORCE_QUIET_HOURS` | `false` | Reject calls outside window |
| `CALLING_HOUR_START` | `9` | Window start (local time, inclusive) |
| `CALLING_HOUR_END` | `21` | Window end (local time, exclusive) |
| `CALLING_TIMEZONE` | `Europe/Moscow` | Timezone for quiet hours |
| `VAPI_API_KEY` | — | Vapi API key |
| `VAPI_ASSISTANT_ID` | — | Vapi assistant ID |
| `VAPI_PHONE_NUMBER_ID` | — | Vapi phone number ID |
| `VAPI_SERVER_URL` | — | Public URL for Vapi webhooks |
| `VAPI_WEBHOOK_SECRET` | — | HMAC secret for webhook validation |
| `GEMINI_API_KEY` | — | Google AI Studio API key (Direct mode) |
| `GEMINI_MODEL_ID` | `gemini-2.5-flash-native-audio-preview-12-2025` | Gemini Live model |

See `.env.example` for complete list.

---

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health` | — | Liveness probe |
| GET | `/ready` | — | Readiness (DB + Redis) |
| POST | `/v1/calls` | X-API-Key | Create outbound call |
| GET | `/v1/calls/active` | — | List active calls |
| GET | `/v1/calls/{id}` | — | Full call + transcript |
| GET | `/v1/calls/{id}/card` | — | Compact live card |
| POST | `/v1/calls/{id}/steer` | X-API-Key | Send steering instruction |
| POST | `/v1/calls/{id}/stop` | X-API-Key | Stop call |
| POST | `/v1/calls/{id}/transfer` | X-API-Key | Initiate warm transfer |
| GET | `/v1/calls/{id}/manager-context` | — | Transfer manager context |
| POST | `/webhooks/vapi` | HMAC-SHA256 | Vapi event webhook |
| POST | `/v1/webhooks/mango` | guard (secret/signature/IP) | Mango event webhook |

### Example: Create call

```bash
curl -X POST http://localhost:8000/v1/calls \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{"phone": "+79991234567", "mode": "auto"}'
```

### Example: Steer a call

```bash
curl -X POST http://localhost:8000/v1/calls/{id}/steer \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{"instruction": "Ask about the budget", "issued_by": "12345678"}'
```

---

## Security Features

- **API key auth** (`X-API-Key` header on mutating endpoints) — disabled if `API_KEY` is empty
- **Webhook signature** — HMAC-SHA256 validation for Vapi events
- **Quiet hours** — configurable calling window enforcement
- **Deny list** — `blocked_phones` table, checked on every create_call
- **Security headers** — `X-Content-Type-Options`, `X-Frame-Options`, `X-XSS-Protection` on all responses
- **Request IDs** — every request gets `X-Request-ID` for log correlation
- **Audit trail** — immutable `audit_events` table for all state changes
- **Idempotent webhooks** — `vapi_events` table deduplicates by event ID

---

## Call Status Lifecycle

```
CREATED → QUEUED → DIALING → RINGING → IN_PROGRESS
  → NEEDS_TRANSFER → TRANSFERRING → MANAGER_BRIEFING
  → CONNECTED_TO_MANAGER
  → COMPLETED / FAILED / STOPPED (terminal)
```

State machine enforced via `ALLOWED_TRANSITIONS` in `app/models/call.py`.

Rules:
- Any active status → STOPPED (operator can always stop)
- Any active status → FAILED (engine/external failure)
- Steering rejected for terminal statuses
- Transfer rejected for terminal or already-transferring calls

---

## Telegram Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome + help |
| `/help` | Full command list |
| `/call <phone>` | Initiate AI call |
| `/active` | List active calls with buttons |
| `/listen <call_id>` | Live call card |
| `/steer <call_id> <text>` | Send steering instruction |
| `/stop <call_id>` | Stop call |

---

## Engine Selection (RoutingCallEngine)

| Mode | Engine selected | If engine not configured |
|------|----------------|--------------------------|
| `vapi` | VapiCallEngine | **HTTP 502 EngineError** — no silent fallback |
| `direct` | DirectGeminiEngine | **HTTP 502 EngineError** — no silent fallback |
| `auto` | Vapi → Direct → Stub (in order) | Stub used only if nothing configured |

> **Note:** `auto` mode falls back to StubEngine only if neither Vapi nor Gemini credentials are set.
> Explicit `vapi` or `direct` mode with missing credentials raises an error immediately.

---

## Celery Tasks

```bash
# Worker
celery -A app.workers.celery_app worker --loglevel=info -Q default -c 2

# Beat (scheduled tasks)
celery -A app.workers.celery_app beat --loglevel=info

# Monitor
pip install flower
celery -A app.workers.celery_app flower
```

---

## Vapi Setup

1. Go to [app.vapi.ai](https://app.vapi.ai) → create account
2. **Create Assistant** → configure model, voice, system prompt
   - Server URL: `https://your-backend.onrender.com/webhooks/vapi`
   - Set Secret → use as `VAPI_WEBHOOK_SECRET`
   - Copy Assistant ID → `VAPI_ASSISTANT_ID`
3. **Add Phone Number** → copy ID → `VAPI_PHONE_NUMBER_ID`
4. **API Keys** → copy key → `VAPI_API_KEY`

### Local Webhook Testing (ngrok)

```bash
ngrok http 8000
# Copy https URL → set VAPI_SERVER_URL and update Vapi assistant Server URL
```

---

## Production Deployment (Render)

### Automated (Blueprint)

The included `render.yaml` defines all services. Connect your repo to Render and deploy:

1. Go to [render.com](https://render.com) → New → Blueprint
2. Connect your GitHub repo
3. Render reads `render.yaml` and creates:
   - `amo-crm-api` — FastAPI web service (auto-scales)
   - `amo-crm-bot` — Telegram bot worker
   - `amo-crm-worker` — Celery task worker
   - `amo-crm-db` — PostgreSQL 16
   - `amo-crm-cache` — Redis 7
4. Set the required environment variables in the Render dashboard:
   - `TELEGRAM_BOT_TOKEN`
   - `VAPI_API_KEY`, `VAPI_ASSISTANT_ID`, `VAPI_PHONE_NUMBER_ID`
   - `VAPI_SERVER_URL` (your Render API URL, e.g. `https://amo-crm-api.onrender.com`)
   - `VAPI_WEBHOOK_SECRET`

### Manual Setup

```bash
# 1. Set environment variables on Render (or Heroku / Fly.io / VPS)
# 2. Build
pip install -e .
# 3. Run migrations (one-off job)
alembic upgrade head
# 4. Start API
uvicorn app.main:app --host 0.0.0.0 --port $PORT
# 5. Start bot
python -m bot.main
# 6. Start worker
celery -A app.workers.celery_app worker --loglevel=info -Q default -c 2
```

### Production Checklist

- [ ] `ENVIRONMENT=production` and `LOG_FORMAT=json`
- [ ] `API_KEY` set to a strong random value (shared with bot via `BACKEND_API_KEY` env on bot service)
- [ ] `VAPI_WEBHOOK_SECRET` set and matching Vapi assistant config
- [ ] Mango webhook configured to `POST /v1/webhooks/mango` with guard secret/signature
- [ ] `MEDIA_GATEWAY_MODE=esl_rtp` configured and FreeSWITCH ESL reachable
- [ ] RTP/SIP firewall rules opened between Mango, FreeSWITCH, backend
- [ ] `GEMINI_AUDIO_INPUT_ENABLED=true` and Gemini key configured
- [ ] `GEMINI_AUDIO_OUTPUT_ENABLED=true` or ElevenLabs fallback configured
- [ ] `ENFORCE_QUIET_HOURS=true` with correct `CALLING_TIMEZONE`
- [ ] Database migrations run before first deploy
- [ ] Health check URL configured: `/health`
- [ ] Readiness probe: `/ready`
- [ ] Logs shipped to aggregator (Render provides built-in log tailing)

Go-live docs:
- [docs/go_live_checklist.md](docs/go_live_checklist.md)
- [docs/first_call_runbook.md](docs/first_call_runbook.md)
- [docs/troubleshooting.md](docs/troubleshooting.md)

### Scaling Notes

- **API**: stateless — can run multiple replicas safely
- **Bot**: run exactly 1 replica (long-polling, no multi-instance)
- **Worker**: scale by adding replicas (Celery is multi-worker safe)
- **DirectSessionManager**: in-memory — single-process only. For multi-replica API, either use sticky sessions or migrate to Redis-backed session store (Phase 3)

---

## Component Readiness

> See [docs/engineering_readiness_audit.md](docs/engineering_readiness_audit.md) for the full audit.

| Component | Status | Real calls? | Notes |
|-----------|--------|-------------|-------|
| `VapiCallEngine` | ✅ PRODUCTION_READY | Yes | Requires VAPI credentials |
| `VapiEventProcessor` | ✅ PRODUCTION_READY | Yes (webhooks) | tool-calls + transfer-dest incomplete |
| `RoutingCallEngine` / `CallRoutePolicy` | ✅ PRODUCTION_READY | Dispatches | Raises on explicit unconfigured mode |
| `DirectGeminiEngine` | ⚠️ INTEGRATION_READY | Partial | Real audio loop wired; live PSTN validation still required |
| `MangoTelephonyAdapter` | ⚠️ INTEGRATION_READY | originate/bridge/whisper/webhook | Control-plane only; no media streaming |
| `FreeSwitchMediaGateway` | ⚠️ INTEGRATION_READY | ESL+RTP implemented | Inbound real path + partial outbound; needs live contour validation |
| `TransferService` | ⚠️ INTEGRATION_READY | Logic complete | Uses `MangoTransferEngine` when Mango configured, otherwise stub |
| `StubEngine` | 🔴 MOCK_ONLY | No | Dev/test only |
| `StubTransferEngine` | 🔴 MOCK_ONLY | No | Used only when Mango transfer engine is not configured |
| `ElevenLabsClient` | ⚠️ INTEGRATION_READY | API path implemented | Requires live call validation (latency/quality) |
| Bot commands | ✅ PRODUCTION_READY | Via API | No Telegram user auth (open access) |
| `AbusePolicy` / `RateLimiter` | ✅ PRODUCTION_READY | N/A | Redis fail-open, DB-backed caps |

---

## Known Gaps (Critical)

These are not "Phase 2 nice-to-haves" — they are functional gaps that make features silently broken in production:

1. **Mango warm transfer requires real webhook delivery** — without `/v1/webhooks/mango` events and Redis-backed state, confirmation flows degrade to polling and may timeout.
2. **Direct mode real audio is not production-proven** — `esl_rtp` baseline exists, but live Mango trunk + FreeSWITCH validation is still required.
3. **Direct voice route still not live-validated end-to-end** — code path exists, but live Mango+FreeSWITCH+Gemini audibility is not yet proven.
4. **Vapi `transfer-destination-request` not answered** — Vapi expects a destination phone/SIP URI in the response body; handler returns `{"status": "ok"}`, transfer silently fails.
5. **Vapi tool-calls not dispatched** — when AI calls a tool (CRM lookup, booking), handler logs only; AI stalls or repeats.
6. **Bot has no Telegram user authentication** — any user who discovers the bot can initiate calls, stop active calls, send steering instructions.
7. **`ALLOWED_TRANSITIONS` not enforced in webhook processor** — late-arriving Vapi events can set calls to invalid states.

---

## What's Implemented

### Foundation + Vapi Integration
- [x] FastAPI with layered architecture (models → repos → services → API)
- [x] PostgreSQL models: Call, Manager, Steering, Audit, Transcript, Transfer, VapiEventLog
- [x] 12 call statuses with state machine (`ALLOWED_TRANSITIONS`)
- [x] REST API: create / active / get / card / steer / stop
- [x] Phone normalization to E.164
- [x] AbstractCallEngine → VapiEngine + DirectGeminiEngine + StubEngine + RoutingCallEngine
- [x] Vapi webhook (HMAC-SHA256, idempotent, event routing)
- [x] Transcript streaming (per-utterance rows, sequence_num)
- [x] Warm transfer with manager selection
- [x] Telegram bot with live card, inline buttons

### Stabilization (current)
- [x] Request / correlation IDs on every request (`X-Request-ID`)
- [x] Security headers middleware (`X-Frame-Options`, `X-Content-Type-Options`, etc.)
- [x] API key authentication (`X-API-Key` header, disabled when `API_KEY` is empty)
- [x] Deny list / stop list (`blocked_phones` table + service check)
- [x] Quiet hours enforcement (configurable window + timezone)
- [x] State transition validation (`ALLOWED_TRANSITIONS` dict)
- [x] 326 passing tests
- [x] `render.yaml` blueprint for one-click Render deployment
- [x] `Procfile` for alternative deployment targets

## Known Limitations (Phase 2)

1. **DirectSessionManager is single-process**: in-memory state lost on restart. Fix: Redis-backed session store.
2. **Live production contour not validated**: no signed-off e2e evidence for Mango trunk + FreeSWITCH + Gemini + PSTN playback.
3. **StubTelephonyAdapter**: remains fallback path if Mango is not configured.
4. **Celery reconciliation task**: skeleton only — missed Vapi webhooks not polled.

## Next Steps

1. **CRM Integration**: amoCRM webhook → create/update leads on call completion
2. **Analytics**: call outcome dashboard (conversion rate, avg duration, sentiment trends)
3. **Redis session manager**: horizontal scaling for Direct mode
4. **Audio pipeline hardening**: validate Gemini native audio + ElevenLabs fallback behavior under live PSTN load.
5. **MangoTelephonyAdapter**: run live tenant smoke for webhook semantics and warm transfer event timing.
6. **FreeSWITCH RTP media path (NEEDS_REAL_WORLD_VALIDATION)**: `esl_rtp` baseline is implemented, but deployment-specific command templates, NAT/SRTP, jitter and latency must be validated on real contour.
7. **Mango manager cooldown restore is durable**: deadline is persisted (`managers.available_after`) and restored by periodic reconciliation (API lifespan loop / Celery beat). In-process timer is retained as a best-effort fast path.
8. **Go-live sign-off**: execute first-call runbook and attach live evidence/log excerpts for answer, first AI reply, stop, transfer.

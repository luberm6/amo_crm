# Engineering Readiness Audit

**Date:** 2026-04-03  
**Status:** Living document — update after each integration milestone  
**Scope:** All integration layers, call engines, transfer, voice, bot, webhooks

---

## TL;DR — Readiness Matrix

| Component | Status | Real calls? | Real audio? | Notes |
|-----------|--------|-------------|-------------|-------|
| `VapiCallEngine` | ✅ PRODUCTION_READY | Yes | Via Vapi SaaS | Requires VAPI credentials |
| `VapiEventProcessor` | ✅ PRODUCTION_READY | Yes (webhooks) | N/A | Two incomplete handlers (see §5) |
| `RoutingCallEngine` | ✅ PRODUCTION_READY | Dispatches | N/A | Stable routing via `call.route_used` |
| `CallRoutePolicy` | ✅ PRODUCTION_READY | N/A | N/A | Now raises on explicit unconfigured mode |
| `DirectGeminiEngine` | ⚠️ INTEGRATION_READY | No real call | No | WebSocket + text only. Audio = Phase 2 |
| `MangoTelephonyAdapter` | ⚠️ INTEGRATION_READY | Originate/bridge | No | `audio_stream()` → `NotImplementedError` |
| `TransferService` | ⚠️ INTEGRATION_READY | Logic complete | N/A | Runs on `StubTransferEngine` — no real dial |
| `StubEngine` | 🔴 MOCK_ONLY | No | No | Dev/test use only |
| `StubTelephonyAdapter` | 🔴 MOCK_ONLY | No | Silence | Dev/test use only |
| `StubTransferEngine` | 🔴 MOCK_ONLY — ALWAYS ACTIVE | No | N/A | **Critical: always used in production** |
| `ElevenLabsClient` | 🔴 MOCK_ONLY | No | Returns silence | API calls commented out (Phase 2) |
| `StubVoiceProvider` | 🔴 MOCK_ONLY | No | Silence | Always active, no config bypass |
| `GeminiLiveClient` | ⚠️ INTEGRATION_READY | WebSocket real | TEXT only | send_audio() is no-op (Phase 2) |
| Bot commands/callbacks | ✅ PRODUCTION_READY | Via API | N/A | No Telegram auth (any user can access) |
| `CallService` | ✅ PRODUCTION_READY | N/A | N/A | Deny list, quiet hours, rate limiting |
| `AbusePolicy` / `RateLimiter` | ✅ PRODUCTION_READY | N/A | N/A | Redis fail-open, DB-backed caps |
| Webhook HMAC validation | ✅ PRODUCTION_READY | N/A | N/A | Enforces signature when secret configured |
| State machine (`ALLOWED_TRANSITIONS`) | ⚠️ NEEDS_VALIDATION | N/A | N/A | Declared, not enforced in webhook processor |

---

## §1 — Call Engine Layer

### VapiCallEngine — ✅ PRODUCTION_READY

**File:** `app/integrations/vapi/engine.py`  
**What works:**
- `POST /call/phone` — creates a real outbound call via Vapi SaaS
- `DELETE /call/{id}` — terminates a live call
- `POST /call/{id}/say` — injects a message into the live call (steering)
- `GET /call/{id}` — polls call status
- Passes `internal_call_id` as Vapi metadata for webhook correlation

**Requires:** `VAPI_API_KEY`, `VAPI_ASSISTANT_ID`, `VAPI_PHONE_NUMBER_ID`  
**Has not been end-to-end tested:** Webhook correlation (`internal_call_id` → our call record) against a live Vapi account.

---

### DirectGeminiEngine — ⚠️ INTEGRATION_READY (text only)

**File:** `app/integrations/direct/engine.py`  
**What works:**
- Opens real WebSocket to `wss://generativelanguage.googleapis.com`
- Sends text instructions to Gemini
- Receives text responses, persists to TranscriptEntry
- Session lifecycle (start, stop, flush)

**What doesn't work:**
- **No real outbound phone call.** When `mode=direct`, no telephone rings anywhere.
- `GeminiLiveClient.send_audio()` is a no-op (Phase 2 code commented out)
- `response_modalities: ["TEXT"]` — Gemini responds in text, not audio
- Gemini audio output → `telephony.send_audio()` → either `StubTelephonyAdapter` (silently drops) or `MangoTelephonyAdapter` (`NotImplementedError`)

**Risk:** Presenting Direct mode as a calling feature to customers is misleading. It's a Gemini text session with no telephone connectivity.

**Path to production:**
1. Implement Mango Click-to-Call + WebSocket audio bridge
2. Uncomment `send_audio()` in `GeminiLiveClient`
3. Set `response_modalities: ["AUDIO"]`
4. End-to-end test: Mango dials phone → audio flows → Gemini responds → audio back to phone

---

### StubEngine — 🔴 MOCK_ONLY

**File:** `app/integrations/call_engine/stub.py`  
**Used as:** `fallback_engine` in `RoutingCallEngine` (always instantiated, used when no real engine selected)  
**Behavior:** Returns `stub-{call_id}` as external_id, sets status QUEUED. No real telephony.  
**Guard:** `RoutingCallEngine` selects stub only for `AUTO` mode with no configured engines. For explicit `VAPI`/`DIRECT` mode without configured engine: now raises `EngineError` (bug fix applied).

---

### RoutingCallEngine / CallRoutePolicy — ✅ PRODUCTION_READY

**Files:** `app/integrations/call_engine/router_engine.py`, `route_policy.py`  
**What works:** Stable routing via `call.route_used`, Vapi→Direct fallback in AUTO mode, observability logs.  
**Fixed bug:** VAPI/DIRECT mode without configured engine now raises `EngineError` instead of silently falling to Stub.

---

## §2 — Transfer Layer

### TransferService — ⚠️ INTEGRATION_READY (logic complete, engine is stub)

**File:** `app/services/transfer_service.py`  
**What works (logic):**
- SELECT FOR UPDATE race condition protection
- Multi-manager retry loop (up to `transfer_max_manager_attempts`)
- Client hangup detection before dial and before bridge
- `asyncio.wait_for` timeouts on dial/whisper/bridge
- All failure stages: no_managers, dial, dial_timeout, bridge, bridge_timeout, caller_dropped
- Full audit trail with `failure_stage` column

**What doesn't work:**
- Every call to `engine.initiate_manager_call()`, `engine.play_whisper()`, `engine.bridge_calls()` goes to `StubTransferEngine`
- `StubTransferEngine` immediately returns simulated success
- **No manager phone rings. Ever. In any deployment.**

**Risk:** `POST /calls/{id}/transfer` returns HTTP 201 with `status=CONNECTED`. This looks successful. No manager was dialed. Customer was not bridged. This is silent failure at service level.

---

### StubTransferEngine — 🔴 MOCK_ONLY — ⚠️ ALWAYS ACTIVE IN PRODUCTION

**File:** `app/integrations/transfer_engine/stub.py`  
**DI wire:** `app/api/deps.py:get_transfer_engine()` — unconditionally returns `StubTransferEngine()`  
**There is no production transfer engine implementation.**

**Path to production:**
1. Implement `VapiTransferEngine` using Vapi squad/transfer API (for Vapi-mode calls)
2. Implement `MangoTransferEngine` using Mango SIP BRIDGE (for Direct-mode calls)
3. Wire in `deps.py` based on `call.route_used`

---

## §3 — Telephony Layer

### MangoTelephonyAdapter — ⚠️ INTEGRATION_READY (telephony control only)

**File:** `app/integrations/telephony/mango.py`  
**Real HTTP calls to** `https://app.mango-office.ru/vpbx`:

| Method | Endpoint | Works? |
|--------|----------|--------|
| `originate_call()` | `POST /commands/callback` | ✅ |
| `terminate_leg()` | `POST /commands/hangup` | ✅ |
| `get_leg_state()` | `GET /stats/request` | ✅ |
| `play_whisper()` | `POST /commands/play` | ✅ |
| `bridge_legs()` | `POST /commands/transfer` | ✅ |
| `audio_stream()` | — | ❌ `NotImplementedError` |
| `send_audio()` | — | ❌ `NotImplementedError` |

**Note:** Mango REST API does not support bidirectional PCM audio streaming. Audio would require a separate SIP-level integration (Asterisk/FreeSWITCH bridge or Mango SIP trunk). This is fundamentally a Phase 2 infrastructure decision.

**Current use:** MangoTelephonyAdapter is instantiated in `deps.py` when Gemini + Mango are configured and is used for control-plane in Direct mode. Media path is available via separate media-gateway modes (`mock`/`esl_rtp`) and still requires live validation.

---

### StubTelephonyAdapter — 🔴 MOCK_ONLY

**File:** `app/integrations/telephony/stub.py`  
**Used:** Always, in all test and dev environments  
**Behavior:** Returns fake leg IDs, `audio_stream()` yields silence, `send_audio()` silently drops bytes  
**Guard:** None — used even in production if GEMINI_API_KEY is set but MANGO credentials are not

---

## §4 — Voice Provider Layer

### StubVoiceProvider — 🔴 MOCK_ONLY (always active)

**File:** `app/integrations/voice/stub.py`  
**Always wired in** `deps.py` regardless of `ELEVENLABS_ENABLED` setting.  
**Behavior:** Returns 100ms of silence.

### ElevenLabsClient — ⚠️ INTEGRATION_READY

**File:** `app/integrations/voice/elevenlabs.py`  
**Status:** real HTTP calls are implemented and DI wiring enables this provider when configured, but end-to-end PSTN validation remains required.
**Status:** Class structure complete, httpx client initialized, but all API calls are commented out.  
**`synthesize()`:** Returns `b"\x00" * 3200` (silence) with a `log.warning("elevenlabs.synthesize.stub")`  
**`synthesize_streaming()`:** Yields one chunk of silence.

**Important:** `ELEVENLABS_ENABLED=true` config exists. `settings.elevenlabs_configured` property exists. **But `deps.py` ignores these and always uses `StubVoiceProvider`.** Setting `ELEVENLABS_ENABLED=true` in production has zero effect.

**Path to production:**
1. Uncomment API calls in `ElevenLabsClient`
2. Wire in `deps.py` when `settings.elevenlabs_configured`
3. Connect to Direct mode audio pipeline (requires Phase 2 audio path)

---

## §5 — Webhook Layer

### VapiEventProcessor — ✅ PRODUCTION_READY (with two incomplete handlers)

**File:** `app/integrations/vapi/event_processor.py`  
**Handles correctly:**
- `transcript` → `TranscriptEntry` (final only, real-time + end-of-call bulk)
- `status-update` → `CallStatus` mapping
- `end-of-call-report` → COMPLETED/FAILED + summary + sentiment
- `hang` → FAILED

**Incomplete handlers:**

#### `_handle_tool_calls` — logs only
When Vapi AI calls a tool (e.g., CRM lookup, booking), the handler logs the tool name and does nothing else. The AI will receive no response and may stall or repeat.

**Risk:** If Vapi assistant is configured with any tools, they will never execute. Calls may stall.

#### `_handle_transfer_request` — sets NEEDS_TRANSFER but no destination returned
When Vapi sends `transfer-destination-request`, the system sets `call.status = NEEDS_TRANSFER` and returns `{"status": "ok"}`. **Vapi is expecting a destination phone number or SIP URI in the response body.** Without it, Vapi cannot complete the transfer.

**Risk:** If Vapi is configured for automatic transfer (AI decides to transfer), the transfer will silently fail — Vapi receives no destination.

---

## §6 — Bot Layer

### Bot commands / callbacks — ✅ PRODUCTION_READY (no auth)

**Files:** `bot/handlers/commands.py`, `bot/handlers/callbacks.py`  
**All commands work:** `/call`, `/active`, `/listen`, `/steer`, `/stop`, inline keyboard buttons  
**Rate limiting errors (429):** Handled with friendly Russian messages  

**Missing: Telegram user authentication**  
The bot has no whitelist of allowed Telegram users. Any user who knows the bot's username can:
- Initiate outbound calls
- Stop active calls
- Send steering instructions
- Initiate warm transfers

**Risk in production:** Unauthorized users can run up telephony costs and disrupt active sales calls.

---

## §7 — State Machine

### ALLOWED_TRANSITIONS — ⚠️ DECLARED, partially enforced

**File:** `app/models/call.py`  
`ALLOWED_TRANSITIONS` dict correctly models the 12-status lifecycle.

**Where it IS enforced:**
- `TransferService` — checks `is_in_transfer()`, `is_terminal()` before proceeding
- `CallService.steer_call()` — checks `is_terminal()`
- `CallService.stop_call()` — checks `status in TERMINAL_STATUSES`

**Where it is NOT enforced:**
- `VapiEventProcessor._handle_status_update()` — applies any Vapi status mapping without checking `ALLOWED_TRANSITIONS`. A Vapi `status-update: ended` on a STOPPED call would silently set `call.status = COMPLETED`.
- No call to `Call.transition_to()` or equivalent in any webhook path.

**Risk:** Unexpected status sequences from Vapi (e.g., late-arriving events) can set calls to invalid states.

---

## §8 — Critical Gaps Summary

| # | Gap | Risk Level | Path to Fix |
|---|-----|------------|-------------|
| 1 | **Warm transfer always uses StubTransferEngine** — no manager is ever dialed | 🔴 Critical | Implement VapiTransferEngine or MangoTransferEngine |
| 2 | **Direct mode audio path is not production-validated** — baseline `esl_rtp` exists but no live proof on Mango tenant contour | 🔴 Critical | Run live e2e smoke: customer speech -> Gemini -> PSTN playback |
| 3 | **ElevenLabs runtime needs live credential and latency validation** | 🟡 High | Validate real TTS in live call contour and alerting |
| 4 | **Vapi transfer-destination-request** returns no destination | 🟡 High | Implement destination selection in `_handle_transfer_request` |
| 5 | **Vapi tool-calls not dispatched** — AI tools never execute | 🟡 High | Implement tool dispatch layer |
| 6 | **Bot has no Telegram auth** — any user can control calls | 🟡 High | Add `TELEGRAM_ALLOWED_USERS` config + guard |
| 7 | **ALLOWED_TRANSITIONS not enforced in webhook processor** | 🟠 Medium | Add transition validation in `_handle_status_update` |
| 8 | **ElevenLabs never wired** — config exists but has zero effect | 🟠 Medium | Wire in deps.py when `elevenlabs_configured` |

---

## §9 — What Needs Real-World Validation

These are items that pass all unit tests but have never been validated end-to-end with real credentials and real phone calls:

1. **Full Vapi call flow:** POST /calls → Vapi dials → webhook events → COMPLETED in DB → transcript saved
2. **Webhook correlation:** `internal_call_id` in Vapi metadata → `get_by_vapi_id()` finds correct call record
3. **Steering delivery:** `steer_call` → Vapi `/say` endpoint → AI actually speaks the instruction
4. **Mango originate_call:** Mango API signs request correctly, phone rings
5. **Rate limiting under load:** Redis Lua script atomicity under concurrent requests
6. **Transfer SELECT FOR UPDATE:** PostgreSQL row-level lock actually prevents double transfer (SQLite in tests silently ignores FOR UPDATE)
7. **DirectGeminiEngine WebSocket:** Reconnect behavior on disconnect, session cleanup on process restart

---

## §10 — How to Detect Stub Usage at Runtime

### Log signals that indicate stub is active:

```
# StubEngine handling a call (not a real call)
stub_engine.initiate_call  call_id=... mode=...

# StubTransferEngine simulating transfer  
stub_transfer_engine.initiate_manager_call  

# ElevenLabs returning silence
elevenlabs.synthesize.stub

# Production deployed with no real engine
production_no_real_engine  (ERROR level)
production_stub_transfer   (WARNING level)
```

### Config check (startup):
When `ENVIRONMENT=production`, startup logs will now emit warnings if stub engines are active. Check the first 10 lines of startup logs for `production_stub_*` entries.

---

## Appendix — Capability Matrix

### Vapi Mode (`mode=vapi`)
| Feature | Supported |
|---------|-----------|
| Outbound phone call | ✅ Real |
| Real-time transcript | ✅ Real |
| Steering (inject message) | ✅ Real |
| End-of-call summary | ✅ Real |
| Tool calls (CRM, booking) | ❌ Not dispatched |
| Warm transfer to manager | ❌ Stub (no real dial) |

### Direct Mode (`mode=direct`)
| Feature | Supported |
|---------|-----------|
| Outbound phone call | ❌ No real call |
| Gemini WebSocket session | ✅ Real |
| Text transcript | ✅ Real |
| Steering | ✅ Real (text injection) |
| Audio input from phone | ❌ No (Phase 2) |
| Audio output to phone | ❌ No (Phase 2) |
| Warm transfer | ❌ Stub (no real dial) |

### Warm Transfer (all modes)
| Feature | Supported |
|---------|-----------|
| Manager selection logic | ✅ Logic complete |
| Race condition protection | ✅ SELECT FOR UPDATE |
| Timeout handling | ✅ asyncio.wait_for |
| Audit trail | ✅ Complete |
| Actual manager dial | ❌ StubTransferEngine |
| Manager hears whisper | ❌ StubTransferEngine |
| Bridge established | ❌ StubTransferEngine |

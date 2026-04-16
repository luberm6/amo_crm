# Telephony Architecture

## Overview

The system supports two call routes for AI-driven outbound calls:

```
Route A (Vapi):
  API/Telegram → CallService → RoutingCallEngine → VapiCallEngine → Vapi SaaS → Mango SIP → PSTN → Customer

Route B (Direct):
  API/Telegram → CallService → RoutingCallEngine → DirectGeminiEngine → Gemini Live WS
                                                  ↘ MangoTelephonyAdapter → Mango REST → PSTN → Customer
```

Route A uses Vapi as the AI platform — Vapi manages the SIP trunk, TTS, and webhook delivery. Route B (Direct) uses Google Gemini Live API for AI with Mango Office REST API for telephony control.

---

## CallRoutePolicy

File: `app/integrations/call_engine/route_policy.py`

Answers "which engine route should handle this call?" with documented, testable rules.

**Selection rules by `CallMode`:**

| Mode | Rule |
|------|------|
| `VAPI` | Always use Vapi. If not configured → Stub (dev) |
| `DIRECT` | Always use Direct. If not configured → Stub (dev) |
| `AUTO` | Vapi first → Direct second → Stub last |

**Fallback policy:**

| From | To | Condition |
|------|----|-----------|
| `vapi` | `direct` | Allowed in AUTO mode, or if `allow_vapi_to_direct_fallback=True` |
| `direct` | `stub` | Never in production |
| `stub` | any | No further fallback |

VAPI mode with an explicit intent means explicit failure — no silent fallback to Direct.

---

## RoutingCallEngine

File: `app/integrations/call_engine/router_engine.py`

**Key design decisions:**

### 1. Stable routing for existing calls

`stop_call()`, `send_instruction()`, and `get_status()` must reach the same engine that *created* the call. Without this, an AUTO mode call created via Vapi could be stopped via Direct if Vapi is reconfigured.

Solution: `call.route_used` is stored on first `initiate_call()`. All subsequent operations use `_resolve_for_existing_call(call)` which reads `call.route_used` instead of re-evaluating the policy.

```python
def _resolve_for_existing_call(self, call: Call) -> AbstractCallEngine:
    route = self._policy.resolve_for_existing_call(call)  # uses call.route_used
    return self._engine_by_name(route)
```

### 2. Vapi → Direct fallback

If `VapiCallEngine.initiate_call()` raises `EngineError` and the policy permits it, the router retries with `DirectGeminiEngine`. The fallback is recorded in `result.metadata["fallback"]` for audit.

```python
except EngineError:
    fallback_route = self._policy.allows_fallback(call, primary_route)
    if fallback_route:
        result = await fallback_engine.initiate_call(call)
        result.metadata["fallback"] = {"from_route": primary_route, ...}
```

### 3. Observability

Every routing decision emits a structured log event:
- `routing_engine.initiate_call` — selected route, engine class, call ID
- `routing_engine.fallback` — from/to routes, primary error
- `routing_engine.call_initiated` — final route_used, external_id

---

## ID Mapping Layer

Three IDs track a call through the system:

| Field | Stored on | Meaning |
|-------|-----------|---------|
| `call.id` | `calls.id` | Internal UUID, primary key |
| `call.vapi_call_id` | `calls.vapi_call_id` | Vapi's call ID for webhooks, stop, steer |
| `call.mango_call_id` | `calls.mango_call_id` | Direct: Gemini session ID; Mango: Mango leg UID |
| `call.telephony_leg_id` | `calls.telephony_leg_id` | SIP Call-ID or Mango leg UID for SIP tracing |
| `call.route_used` | `calls.route_used` | Which engine created the call: "vapi" / "direct" / "stub" |

**Mapping at creation:**
```python
# In CallService.create_call():
call.route_used = result.route_used
if call.mode == CallMode.DIRECT:
    call.mango_call_id = result.external_id  # Gemini session_id
else:
    call.vapi_call_id = result.external_id   # Vapi call ID

if result.telephony_leg_id:
    call.telephony_leg_id = result.telephony_leg_id  # SIP correlation
```

**Cross-correlation:**
- Vapi webhooks arrive with `vapi_call_id` → lookup `calls.vapi_call_id`
- Mango SIP events arrive with SIP `Call-ID` → lookup `calls.telephony_leg_id`
- Internal operations use `call.id`

---

## MangoTelephonyAdapter

File: `app/integrations/telephony/mango.py`

Production adapter for Mango Office REST API.

### Contract

| Method | Mango API | Status |
|--------|-----------|--------|
| `originate_call()` | POST `/commands/callback` (Click-to-Call) | ✅ |
| `terminate_leg()` | POST `/commands/hangup` | ✅ idempotent |
| `get_leg_state()` | Cache + GET `/stats/request` | ✅ |
| `play_whisper()` | POST `/commands/play` (TTS prefix) | ✅ |
| `bridge_legs()` | POST `/commands/transfer` | ✅ |
| `connect()` | wraps `originate_call()` | ✅ |
| `audio_stream()` | — | ❌ NotImplementedError (Phase 2) |
| `send_audio()` | — | ❌ NotImplementedError (Phase 2) |

### Auth

All requests use HMAC-SHA256 signing:
```
sign = SHA256(api_key + api_salt + json_params)
POST body: vpbx_api_key={api_key}&sign={sign}&json={json_params}
```

### Known SIP/Mango Integration Risks

1. **Delayed answer events** — Mango fires DTMF/answer events up to 10s after the API call. `get_leg_state()` may return `INITIATING` for several seconds. Poll with backoff.

2. **Duplicate events** — `RINGING` may arrive 2–3 times. All state updates must be idempotent.

3. **Leg ID mismatch** — Mango uses numeric UIDs in REST API; SIP uses string `Call-ID`. Both are stored: `mango_call_id` for REST, `telephony_leg_id` for SIP logs.

4. **Bridge race condition** — Both legs must be in `ANSWERED` state before bridging. `bridge_legs()` checks state; on failure it terminates the manager leg to prevent orphans.

5. **Hangup ordering** — Mango BYE arrives before the webhook. Check state before calling `terminate_leg()`.

6. **Audio streaming** — Mango REST has no PCM streaming. Phase 2 requires FreeSWITCH/Asterisk ESL bridge.

---

## Known Limitations

### 1. Single-process DirectSessionManager

`DirectSessionManager` keeps sessions in an asyncio-friendly in-memory dict. On restart, all active Direct calls are lost. With >1 replica, sticky routing or Redis pub/sub is needed (Phase 3).

### 2. Audio streaming (Phase 2)

`MangoTelephonyAdapter.audio_stream()` raises `NotImplementedError`. For Gemini Live + Mango telephony (Route B with real audio), a SIP media bridge is required:
- FreeSWITCH `mod_event_socket` (ESL) — bridge Mango SIP ↔ Python
- Asterisk AGI/ARI — similar approach
- WebRTC gateway

Until Phase 2, Route B uses `StubTelephonyAdapter` for audio (no real call placed).

### 3. Gemini model ID

Default: `gemini-2.5-flash-native-audio-preview-12-2025`. Verify availability in [Google AI Studio](https://aistudio.google.com) before production deploy. Configure via `GEMINI_MODEL_ID` env var.

### 4. Vapi webhook statelessness vs Direct statefulness

Vapi route is stateless from the backend's perspective — events arrive via webhooks. Direct route is stateful — sessions live in-process. They are not interchangeable at the infrastructure level.

---

## Configuration

```env
# Vapi Route A
VAPI_API_KEY=...
VAPI_ASSISTANT_ID=...
VAPI_PHONE_NUMBER_ID=...

# Direct Route B
GEMINI_API_KEY=...
GEMINI_MODEL_ID=gemini-2.5-flash-native-audio-preview-12-2025   # optional
GEMINI_API_VERSION=v1beta                   # optional

# Mango (used in Route B for telephony control)
MANGO_API_KEY=...
MANGO_API_SALT=...
MANGO_FROM_EXT=101                          # extension to call first
```

Availability is determined at startup in `deps.py`:
- `vapi_configured` → `VapiCallEngine` is instantiated
- `gemini_configured` → `DirectGeminiEngine` is instantiated; uses `MangoTelephonyAdapter` if `mango_configured`, else `StubTelephonyAdapter`

---

## Manual Smoke Checklist

### Route A: Vapi

```
1. Set VAPI_API_KEY, VAPI_ASSISTANT_ID, VAPI_PHONE_NUMBER_ID
2. POST /v1/calls {"phone": "+79991234567", "mode": "vapi"}
   → response.route_used == "vapi"
   → response.vapi_call_id is set
3. GET /v1/calls/{id}
   → status changes: QUEUED → RINGING → IN_PROGRESS (via webhooks)
4. POST /v1/calls/{id}/steer {"instruction": "Say hello in English"}
   → 200, call AI responds in English
5. POST /v1/calls/{id}/stop
   → status: STOPPED
6. Check Vapi dashboard — call visible in call logs
7. Check SIP logs — telephony_leg_id correlates with SIP Call-ID
```

### Route B: Direct (with Stub Telephony)

```
1. Set GEMINI_API_KEY (no Mango needed for stub telephony)
2. POST /v1/calls {"phone": "+79991234567", "mode": "direct"}
   → response.route_used == "direct"
   → response.mango_call_id is set (Gemini session_id)
   → status: IN_PROGRESS (Gemini session started immediately)
3. POST /v1/calls/{id}/steer {"instruction": "Ask about budget"}
   → 200, injected into Gemini session queue
4. GET /v1/calls/{id}
   → transcript_entries populate as Gemini responds
5. POST /v1/calls/{id}/stop
   → status: STOPPED, Gemini session terminated
```

### AUTO mode fallback verification

```
1. Set only GEMINI_API_KEY (no VAPI_API_KEY)
2. POST /v1/calls {"phone": "+79991234567", "mode": "auto"}
   → route_used == "direct" (Vapi not configured, fell back to Direct)

3. Set only VAPI_API_KEY, temporarily make Vapi endpoint fail (mock)
4. POST /v1/calls {"phone": "+79991234567", "mode": "auto"}
   → route_used == "direct" (Vapi failed, fell back to Direct)
   → result.metadata.fallback.from_route == "vapi"
```

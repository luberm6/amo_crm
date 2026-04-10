# Telephony Platform Architecture

**Date:** 2026-04-03  
**Status:** Phase 2 — provider-agnostic via capability model and registry  
**Files:** `app/integrations/telephony/`

---

## 10.1 — What Was Dangerous About Mango Hard-Wiring

Before this change, `deps.py` contained:

```python
if settings.mango_configured:
    telephony = MangoTelephonyAdapter()
else:
    telephony = StubTelephonyAdapter()
```

This created a two-way coupling that could not scale:

| Risk | Impact |
|------|--------|
| **Only one real provider** | Replacing Mango requires editing `deps.py`, `get_call_engine()`, and possibly `TransferService` |
| **No capability negotiation** | Code that calls `audio_stream()` would get `NotImplementedError` from Mango without any early warning |
| **Silent degradation** | Mango config missing → silently falls to Stub → calls appear to work but do nothing |
| **No provider documentation** | What Mango supports vs doesn't is scattered across docstrings in mango.py |
| **No second-provider template** | Adding Twilio requires designing the entire integration from scratch without guidance |

---

## 10.2 — New Provider-Agnostic Architecture

### Components

```
┌──────────────────────────────────────────────────────────────────────┐
│  Business Logic (does not know about Mango/Twilio/SIP)               │
│  DirectGeminiEngine, TransferService, CallService                     │
└──────────────────────────┬───────────────────────────────────────────┘
                           │ uses AbstractTelephonyAdapter
┌──────────────────────────▼───────────────────────────────────────────┐
│  TelephonyProviderRegistry                                            │
│  register("mango", MangoTelephonyAdapter)                             │
│  register("stub", StubTelephonyAdapter)                               │
│  resolve("auto") → MangoTelephonyAdapter (if configured)             │
└──────────────────────────┬───────────────────────────────────────────┘
                           │ returns
┌───────────────┬──────────▼──────────┬─────────────────────┐
│  Stub         │  Mango              │  Twilio (skeletal)  │
│  capabilities │  capabilities       │  capabilities       │
│  audio: ✅    │  audio: ❌          │  audio: ✅ (TODO)   │
│  bridge: ✅   │  bridge: ✅         │  bridge: ✅ (TODO)  │
└───────────────┴─────────────────────┴─────────────────────┘
```

### Files

| File | Purpose |
|------|---------|
| `telephony/base.py` | `AbstractTelephonyAdapter` + `capabilities` abstract property |
| `telephony/capabilities.py` | `ProviderCapabilities` dataclass + `UnsupportedOperationError` |
| `telephony/registry.py` | `TelephonyProviderRegistry` + `build_default_registry()` |
| `telephony/mango.py` | Mango implementation + capabilities declaration |
| `telephony/stub.py` | Stub for dev/tests + capabilities declaration |
| `telephony/twilio.py` | Skeletal Twilio adapter (template for new providers) |

### Key principle: fail loud, not silent

Before: calling `audio_stream()` on Mango raised `NotImplementedError` deep inside the audio loop.  
After: call `adapter.capabilities.check("audio_stream")` at session creation time → `UnsupportedOperationError` (HTTP 422) before the WS is opened.

---

## 10.3 — Provider Capability Matrix

| Capability | Stub | Mango | Twilio (skeletal) | SIP-generic |
|-----------|------|-------|-------------------|-------------|
| `supports_outbound_call` | ✅ | ✅ | ✅ (TODO) | ✅ |
| `supports_audio_stream` | ✅ (silence) | ❌ Phase 2 | ✅ (TODO) | ✅ RTP |
| `supports_bridge` | ✅ | ✅ | ✅ (TODO) | ✅ |
| `supports_whisper` | ✅ | ✅ | ✅ (TODO) | ✅ |
| `supports_call_recording_events` | ❌ | ✅ | ✅ (TODO) | depends |
| `supports_sip_trunk` | ❌ | ✅ | ✅ | ✅ |
| `supports_real_time_events` | ❌ | ✅ | ✅ (TODO) | depends |

### Checking capabilities before operations

```python
# Before opening a Direct/Gemini session
adapter = registry.resolve(settings.telephony_provider)
adapter.capabilities.check("audio_stream")   # raises UnsupportedOperationError if Mango
# → now safe to call audio_stream()

# Before warm transfer
adapter.capabilities.check("bridge")
adapter.capabilities.check("whisper")
```

---

## 10.4 — How to Add a New Telephony Provider

### Step 1 — Create the adapter file

```
app/integrations/telephony/yourprovider.py
```

Copy `twilio.py` as the template. It has:
- All 9 abstract methods stubbed with `NotImplementedError`
- A `capabilities` property with all flags set (change to `False` for unimplemented)
- State mapping dict at the bottom
- Step-by-step TODO comments in each method

### Step 2 — Implement the capabilities property first

```python
@property
def capabilities(self) -> ProviderCapabilities:
    return ProviderCapabilities(
        provider_name="yourprovider",
        supports_outbound_call=True,   # Set True only when implemented
        supports_audio_stream=False,   # Be honest — False until it works
        supports_bridge=True,
        supports_whisper=True,
        supports_call_recording_events=True,
        supports_sip_trunk=True,
        supports_real_time_events=True,
        notes="YourProvider adapter — see yourprovider.py for implementation status.",
    )
```

**Rule:** Set a capability flag to `True` only after the method is implemented AND tested.

### Step 3 — Add config fields

In `app/core/config.py`:

```python
# ── YourProvider Telephony ─────────────────────────────────────────────────
yourprovider_api_key: str = ""
yourprovider_api_secret: str = ""

@property
def yourprovider_configured(self) -> bool:
    return bool(self.yourprovider_api_key and self.yourprovider_api_secret)
```

### Step 4 — Register in the default registry

In `app/integrations/telephony/registry.py`, `build_default_registry()`:

```python
if settings.yourprovider_configured:
    from app.integrations.telephony.yourprovider import YourProviderAdapter
    registry.register("yourprovider", YourProviderAdapter)
    log.info("telephony_registry.yourprovider_registered")
```

### Step 5 — Activate via config

```env
TELEPHONY_PROVIDER=yourprovider
YOURPROVIDER_API_KEY=key
YOURPROVIDER_API_SECRET=secret
```

Or use `TELEPHONY_PROVIDER=auto` — the registry picks the first non-stub provider
with `supports_outbound_call=True`.

### Step 6 — Add tests

Create `tests/test_telephony_yourprovider.py`. Test:
- `originate_call()` returns `TelephonyOriginateResult`
- `terminate_leg()` is idempotent on already-terminated legs
- `capabilities.provider_name == "yourprovider"`
- `capabilities.supports_audio_stream` matches implementation reality
- Event normalization (if your provider sends webhooks)

---

## 10.5 — Fallback / Degraded Mode

### Auto-selection fallback chain

```
resolve("auto"):
  1. Iterate registered providers (registration order, skip "stub")
  2. First provider with supports_outbound_call → return it
  3. None found → fall back to "stub" with WARNING log
```

### Feature-level degraded mode

The registry and adapter don't silently degrade — they raise. Business logic decides:

```python
adapter = registry.resolve("auto")

# Option A: abort if feature missing
adapter.capabilities.check("audio_stream")   # raises UnsupportedOperationError

# Option B: degrade gracefully
if adapter.capabilities.supports_audio_stream:
    # Full Direct mode with audio
    await engine.initiate_with_audio(call)
else:
    # Degraded: text-only mode, warn user
    log.warning("telephony.audio_not_supported", provider=adapter.capabilities.provider_name)
    await engine.initiate_text_only(call)
```

### Redis unavailability

If Redis is unavailable at startup, the SessionCoordinator falls back to `InMemorySessionStore` (separate from telephony). The telephony registry is not affected by Redis.

---

## 10.6 — Operational Notes

### Which provider is active?

Check startup logs:
```
telephony_registry.mango_registered        ← Mango credentials found, registered
telephony_registry.built  providers=["stub","mango"]
telephony_registry.auto_selected  provider=mango   ← selected at call creation
telephony_registry.auto_fallback_to_stub   ← no production provider configured
```

### Changing provider at runtime

The registry is built at each call to `get_call_engine()` in `deps.py` (stateless DI).
Changing `TELEPHONY_PROVIDER` takes effect on the next deployment restart.
There is no hot-reload — do not expect in-flight calls to switch providers.

### Provider isolation

Each `registry.get(name)` call returns a **fresh adapter instance**.
Providers with stateful HTTP clients (Mango) are created per-request in `get_call_engine()`.
This is intentional — adapters are lightweight, not long-lived singletons.

---

## Appendix — Universal Telephony Contract

All providers must implement these 9 methods:

| Method | Family | Purpose |
|--------|--------|---------|
| `connect(phone)` | Audio | Initiate call, return `TelephonyChannel` |
| `disconnect(phone)` | Audio | Terminate by phone (idempotent) |
| `audio_stream(channel)` | Audio | Async generator: yield PCM 16kHz mono 16bit |
| `send_audio(channel, pcm)` | Audio | Send PCM to customer |
| `originate_call(phone, ...)` | Control | SIP/REST level outbound call origination |
| `bridge_legs(cust_id, mgr_id)` | Control | Bridge two call legs (warm transfer) |
| `play_whisper(leg_id, msg)` | Control | TTS whisper to manager before bridge |
| `terminate_leg(leg_id)` | Control | Hang up a specific leg (idempotent) |
| `get_leg_state(leg_id)` | Control | Return `TelephonyLegState` for a leg |

Plus the `capabilities` property that declares which methods actually work.

# Direct Session Architecture

**Date:** 2026-04-03  
**Status:** Phase 2 — distributed coordination via Redis, live state still in-process  
**Files:** `app/integrations/direct/session_store.py`, `session_coordinator.py`, `session_manager.py`

---

## 10.1 — What Was Dangerous About the In-Memory Session Manager

The original `DirectSessionManager` stored all active sessions in a plain Python dict:

```python
class DirectSessionManager:
    def __init__(self) -> None:
        self._sessions: dict = {}   # ← only in this process, lost instantly on restart
```

### Failure scenarios

| Scenario | Old behaviour | Impact |
|----------|--------------|--------|
| **Process restart / redeploy** | `_sessions` cleared → all live calls silently lost | Calls appear active in DB but have no backing session; steering and stop fail silently |
| **Multiple workers (≥2 replicas)** | Each worker has its own dict → worker B cannot stop a call started on worker A | `/stop` or `/steer` hits worker B → `session_not_found` warning → instruction silently dropped |
| **OOM / SIGKILL** | Session dict gone, DB record left as `IN_PROGRESS` forever | Zombie calls in DB that never transition to COMPLETED or FAILED |
| **Horizontal scale-up** | New worker has no knowledge of existing sessions | Steering routed to wrong worker; only works if API has sticky sessions configured |
| **Scale-down / rolling deploy** | Worker removed while session active | Call abandoned mid-conversation without DB being updated |

The system looked safe in single-process development but any production event that
touched process boundaries caused silent data corruption.

---

## 10.2 — New Architecture

### Layers

```
┌───────────────────────────────────────────────────────────────┐
│  LIVE STATE (in-process, one worker only — cannot distribute) │
│                                                               │
│  DirectSession:                                               │
│    gemini_client    — WebSocket to Gemini Live                │
│    telephony_channel — SIP / Stub handle                      │
│    bg_task          — asyncio.Task (audio loop)               │
│    instruction_queue — asyncio.Queue (steering buffer)        │
│    stop_event       — asyncio.Event (shutdown signal)         │
└────────────────────────┬──────────────────────────────────────┘
                         │ mirrored via SessionCoordinator
┌────────────────────────▼──────────────────────────────────────┐
│  DISTRIBUTED STATE (Redis — survives restart, multi-worker)   │
│                                                               │
│  session:meta:{id}     HASH   — call_id, phone, status,       │
│                                 worker_id, timestamps  TTL=24h │
│  session:lock:{id}     STRING — owning worker_id      TTL=30s  │
│                                 (renewed by heartbeat every 10s)│
│  sessions:active       SET    — all active session_ids         │
│  session:steer:{id}    PUBSUB — steering channel (ephemeral)   │
└───────────────────────────────────────────────────────────────┘
```

### Class responsibilities

| Class | Responsibility |
|-------|---------------|
| `AbstractSessionStore` | Interface only — no business logic |
| `InMemorySessionStore` | Single-process implementation (tests, Redis-unavailable) |
| `RedisSessionStore` | Production implementation (Lua scripts for atomic ops) |
| `SessionCoordinator` | Owns `worker_id`; registers/releases sessions; runs heartbeat; routes steering; reconciles on startup |
| `DirectSessionManager` | Owns the live `_sessions` dict; delegates distributed state to coordinator |

### Key design principle: fail-open

If the coordinator or Redis is unavailable, `DirectSessionManager` logs at ERROR level
and continues with local-only behaviour. A session that cannot register in Redis is
still created locally and will work for the current process lifetime — it just won't
survive restart or be visible to other workers.

---

## 10.3 — Ownership Model

### Worker identity

Each worker process generates a unique ID at startup:

```python
worker_id = f"worker-{uuid.uuid4().hex[:12]}"
```

This ID is stable for the lifetime of the process and identifies which worker
holds the live WebSocket connection for each session.

### Ownership lock (TTL lease)

```
session:lock:{session_id}  →  "{worker_id}"   EX 30
```

- `acquire_lock`: `SET NX EX 30` — atomic, only the first caller succeeds
- `renew_lock`: Lua check-and-expire — only renews if current value matches `worker_id`
- `release_lock`: Lua check-and-delete — only deletes if current value matches

The lock expires automatically after 30 seconds if the heartbeat stops.
This is the **failure boundary**: a process that dies without a graceful shutdown
will stop renewing, and after 30 seconds the session is detectable as orphaned.

### Heartbeat

Each registered session has a background `asyncio.Task` that runs every 10 seconds:

```
every 10s:  renew_lock(session_id, worker_id, ttl=30)
            → if fails: log ERROR "heartbeat_lost"
```

Timeline: `0s acquire → 10s renew → 20s renew → 30s expire` (if process dies between renews).

### Steering routing

```
POST /steer → DirectGeminiEngine.send_instruction()
           → DirectSessionManager.inject_instruction()
              ├── session in local _sessions dict?
              │   └── YES → direct asyncio.Queue.put()  (0 latency)
              └── NO (session is on another worker)
                  └── coordinator.send_steering()
                      ├── get_lock_owner(session_id) → "worker-b"
                      └── publish("session:steer:{id}", instruction)
                                    ↓
                          worker-b _steering_subscriber task
                          → receives message
                          → local_sessions["id"].instruction_queue.put(instruction)
```

---

## 10.4 — Restart Behaviour

### Graceful shutdown (SIGTERM / normal stop)

```
terminate_session(session_id):
  1. session.stop_event.set()                ← signal audio loop
  2. bg_task.cancel()                        ← kill audio loop
  3. event_handler.flush(timeout=3s)         ← save last transcripts
  4. gemini_client.close()                   ← close WS
  5. coordinator.release_session()           ← update metadata, release lock, remove from active
```

After graceful shutdown: lock released, active set updated, metadata = TERMINATED.  
On next startup: reconciler sees no orphans → no DB changes.

### Ungraceful shutdown (SIGKILL / OOM / crash)

```
(process dies)
  ↓
locks expire after 30s (no heartbeat renew)
  ↓
next worker startup → startup_reconcile():
  scan sessions:active
  for each session_id:
    if get_lock_owner() == None:          ← orphaned
      call = get_by_mango_call_id(session_id)
      if call.status not in TERMINAL:
        call.status = FAILED
        save(call)
      update_status(session_id, FAILED)
      remove_from_active(session_id)
```

### Startup reconciliation flow

```python
# app/main.py lifespan():
coordinator = deps.get_session_coordinator()
async with AsyncSessionLocal() as session:
    repo = CallRepository(Call, session)
    stats = await coordinator.startup_reconcile(repo)
    await session.commit()
# stats: {total_checked, orphaned_failed, already_owned, already_terminal, errors}
```

### What reconciliation does NOT do

- Does not attempt to reconnect/resume live WebSocket sessions.  
  Resuming a live Gemini session is not supported by the Gemini Live API (no session ID
  to resume from). The only correct action is to mark the call FAILED and let the
  operator or customer retry.
- Does not send notifications to the customer.  
  That would require integration with the outbound notification layer (future work).

---

## 10.5 — Operational Risks

### Remaining risks after Phase 2

| Risk | Severity | Mitigation |
|------|----------|-----------|
| **WebSocket cannot be resumed** | 🔴 High | Reconciler marks call FAILED; customer must be re-dialled. No auto-reconnect. |
| **Redis unavailable on startup** | 🟡 Medium | Fails open to InMemorySessionStore with WARNING log. Multi-worker unsafe. |
| **30s window of orphan ambiguity** | 🟡 Medium | After crash, other workers see the session as "owned" for up to 30s. No duplicate ownership — just delayed detection. |
| **Steering for remote session requires Redis** | 🟡 Medium | If Redis is down, cross-worker steering fails silently. Same-worker steering (90%+ of cases) always works. |
| **Active set grows unbounded without cleanup** | 🟠 Low | `cleanup_stale()` can be called from Celery beat. Or TTL on the active set key (future). |
| **In-memory fallback loses sessions on restart** | 🔴 High (without Redis) | Ensure Redis is always deployed. `production_stub_*` startup logs flag this. |

### Log signals to watch

```
# Healthy startup with orphaned sessions cleaned up
session_coordinator.reconcile_complete  orphaned_failed=2 total_checked=3

# Ownership conflict (should be rare / never in normal ops)
session_coordinator.ownership_conflict  session_id=... worker_id=...

# Heartbeat lost (process under heavy load or Redis connectivity issue)
session_coordinator.heartbeat_lost  session_id=... worker_id=...

# Steering dropped (no owner for session)
session_coordinator.steer_no_owner  session_id=...

# Using in-memory fallback (Redis is down)
session_coordinator.using_in_memory_store
```

### Deployment guidance

1. **Always deploy Redis** before `GEMINI_API_KEY` is set.
2. **Rolling deploys**: new workers start, old workers finish their sessions gracefully.
   Set `terminationGracePeriodSeconds ≥ 35` so active sessions get `terminate_session()`
   called before SIGKILL.
3. **Horizontal scaling**: any number of replicas can handle new calls.
   Existing calls stay on the worker that created them (lock enforces this).
4. **Multi-worker steering**: ensure all workers connect to the same Redis instance.

---

## Appendix — Key Constants

| Constant | Value | File |
|----------|-------|------|
| `LOCK_TTL_SECONDS` | 30 | `session_store.py` |
| `HEARTBEAT_INTERVAL` | 10 | `session_store.py` |
| `SESSION_META_TTL` | 86400 (24h) | `session_store.py` |

### Redis key space

| Key | Type | TTL | Purpose |
|-----|------|-----|---------|
| `session:meta:{id}` | HASH | 24h | Session metadata |
| `session:lock:{id}` | STRING | 30s | Ownership lease |
| `sessions:active` | SET | none | Active session enumeration |
| `session:steer:{id}` | (pub/sub) | — | Steering delivery channel |

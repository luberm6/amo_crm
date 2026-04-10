# Mango Warm Transfer Flow

## Scope
This document covers warm transfer through `MangoTransferEngine`:
- manager dial
- answer wait
- whisper briefing
- bridge confirmation
- manager retry
- terminal cleanup

## Successful Flow
1. `TransferService` selects manager candidate by priority.
2. `MangoTransferEngine` reserves the manager atomically (`is_available=false`).
3. Engine dials manager leg via Mango `originate_call`.
4. Engine waits for answer (`wait_manager_answer`) via webhook-first + polling fallback.
5. Engine plays whisper and waits for whisper completion confirmation.
6. Engine bridges manager/customer legs and waits for bridge confirmation.
7. Only after bridge confirmation:
   - `TransferRecord.status = CONNECTED`
   - `Call.status = CONNECTED_TO_MANAGER`

Transfer is considered successful **only** after step 7.

## Failure Flows
- Manager no-answer / dial error:
  - attempt marked failed
  - service retries next manager
  - after all attempts: `FAILED_NO_ANSWER`, call `STOPPED`
- Caller dropped during dial/briefing:
  - manager leg terminated
  - transfer `CALLER_DROPPED`
- Whisper timeout/failure:
  - manager leg terminated
  - transfer terminal failure (`TIMED_OUT` or `BRIDGE_FAILED`, stage `briefing`)
- Bridge command accepted but confirmation missing:
  - bridge treated as failed
  - manager leg terminated
  - transfer terminal failure (`TIMED_OUT` or `BRIDGE_FAILED`)

## Attempt Lifecycle (Observability)
- `selected`
- `calling`
- `answered`
- `whispering`
- `bridged`
- `failed_*` (`failed_calling`, `failed_answered`, `failed_whispering`, `failed_bridged`, `failed_cleanup`)

Engine exposes best-effort progress via `get_transfer_progress(external_id)`.

## Concurrency and Race Handling
- Duplicate transfer requests: blocked by service state guard + DB `SELECT FOR UPDATE` (Postgres).
- One manager in two flows: engine atomic reservation prevents double use.
- Delayed Mango events: answer/bridge/whisper waits are webhook-first with polling fallback.
- Late manager answer after timeout: engine cancellation path terminates manager leg.
- Manager cooldown restore: durable deadline (`available_after`) is persisted in DB; in-process timer is only a fast path.

## Manual Smoke Checklist (Real Mango)
1. Configure Mango credentials and webhook URL `/v1/webhooks/mango`.
2. Start one active customer call with `telephony_leg_id` present.
3. Initiate transfer with two managers (priority 1 and 2).
4. Confirm priority-1 manager phone rings.
5. Reject/timeout first manager, confirm retry to second manager.
6. Answer second manager, confirm whisper is played before bridge.
7. Confirm bridge actually connects manager to customer.
8. Trigger caller hangup during whisper, confirm manager leg terminates.
9. Trigger bridge confirmation miss (disable webhook), confirm transfer fails terminally.
10. Verify DB states and audit trail:
   - `transfer_records.status`
   - `transfer_records.failure_stage`
   - `calls.status`
   - audit events (`transfer_manager_selected`, `transfer_manager_answered`, `transfer_whispering`, `transfer_bridged`).

## Provider Limitations
- Mango webhook schema differs by deployment; normalization may require tenant-specific mapping.
- REST API is control-plane only; no media gateway/RTP path is provided here.

## Current Blockers (Honest)
1. No real end-to-end smoke against a live Mango PBX tenant yet.
2. Live Mango tenant validation is still required to confirm webhook/event semantics in production contour.
3. RTP/media gateway path is outside this control-plane and must be validated separately.

## Ready For Next Stage: Media Gateway
Control-plane for warm transfer is prepared for media-plane integration:
- bridge success is confirmation-based (not optimistic),
- attempt lifecycle and cleanup are explicit,
- concurrency guard for manager reservation is in place.

Next stage can add RTP/SIP media bridge without changing transfer state machine semantics.

# Mango Control Plane

## Scope
This document describes the Mango telephony control plane runtime path:
- outbound originate
- wait-for-answered
- manager whisper
- bridge confirmation
- leg termination
- webhook processing (`POST /v1/webhooks/mango`)

Media streaming is **not** included in this control plane and is still out of scope.

## Originate Flow
1. `MangoTelephonyAdapter.originate_call()` calls `POST /commands/callback`.
2. Provider leg UID is stored in `MangoLegStateStore` with `INITIATING`.
3. Manager dial in transfer uses `MangoTransferEngine.initiate_manager_call()`.
4. `wait_for_answered()` waits for `ANSWERED|BRIDGED`, webhook-first:
   - first checks persistent state updated by webhook processor,
   - then polling fallback (`/stats/request`) if webhook is delayed.

## Answer Flow
1. Mango webhook arrives at `POST /v1/webhooks/mango`.
2. `MangoEventProcessor`:
   - validates and normalizes provider payload,
   - deduplicates by `event_id` (or payload hash),
   - maps provider event to internal leg state model,
   - updates persistent leg state store,
   - correlates internal call/transfer where possible.
3. Awaiters (`wait_for_answered`) are unblocked by updated store state.

## Bridge Flow
1. `bridge_legs(customer_leg_id, manager_leg_id)` checks both legs are answered.
2. Sends Mango transfer command (`POST /commands/transfer`).
3. Does **not** mark success immediately.
4. Waits for bridge confirmation:
   - webhook status `bridge_confirmed` preferred,
   - polling fallback: both legs observed as `BRIDGED`.
5. Only after confirmation, leg states are set to `BRIDGED`.

## Whisper Flow
1. `play_whisper(manager_leg_id, text)` requires manager leg state `ANSWERED`.
2. Sets operation state `whisper_started`.
3. Calls Mango `POST /commands/play`.
4. Waits for webhook-confirmed `whisper_finished`.
5. If timeout/failure: sets `whisper_failed`, raises error.

Transfer service is configured so whisper failure is fatal for that attempt and
the transfer is not marked successful.

## Webhook Flow
Endpoint: `POST /v1/webhooks/mango`

Security guards (configurable):
- `MANGO_WEBHOOK_SECRET` + `X-Mango-Signature` (HMAC-SHA256)
- `MANGO_WEBHOOK_SHARED_SECRET` + `X-Mango-Webhook-Secret` (fallback)
- `MANGO_WEBHOOK_IP_ALLOWLIST` (CIDR/IP list)

Payload normalization handles provider shape variants:
- event keys (`event|event_type|type|status`)
- leg keys (`call_id|uid|recording_id|...`)
- optional correlation keys (`internal_call_id`, `transfer_id`)

## Known Assumptions About Mango API
1. Provider event schema can vary by PBX configuration; parser is defensive.
2. Bridge/whisper confirmation events may differ in naming; normalization is
   alias-based and may need adaptation for tenant-specific payloads.
3. `GET /stats/request` is used as polling fallback and may lag.
4. REST API remains control-plane only; no RTP media path here.
5. Manager reservation/unavailability is persisted, and cooldown restore is
   implemented with durable `managers.available_after` deadline plus periodic
   reconciliation (lifespan loop / Celery beat). In-process timer remains only
   as low-latency fast path.

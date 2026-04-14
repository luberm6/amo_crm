# Mango Live Audit

Date: 2026-04-14  
Method: Real env-backed Mango API calls + backend `sync-lines` + DB verification + agent binding roundtrip  
Tooling: `scripts/mango_live_probe.py`, `docs/mango_live_inventory_sample.json`

## Executive Summary

| Check | Result |
|---|---|
| Mango connectivity | YES |
| Auth / signature | YES |
| Live inventory fetched | YES |
| `sync-lines` live verified | YES |
| Agent binding on live Mango data | YES |
| Repeated `config/users/request` calls stable | NO |

The Mango tenant is reachable with the configured credentials. Real live requests returned:

- 2 incoming lines from `/incominglines`
- 2 parseable extension/user records from `/config/users/request`
- a successful backend `POST /v1/telephony/mango/sync-lines`
- a successful live roundtrip for `PATCH` + `GET /v1/agent-profiles/{id}/settings`

The main operational caveat is **rate limiting on repeated `config/users/request` calls**. The first direct request succeeded with data, but repeated calls in the same probe window returned **HTTP 429**, which also affects `GET /v1/telephony/mango/extensions` during the same run.

## Live Env Diagnostics

| Variable | Status |
|---|---|
| `MANGO_API_KEY` | set |
| `MANGO_API_SALT` | set |
| `MANGO_API_BASE_URL` | `https://app.mango-office.ru/vpbx` |
| `MANGO_FROM_EXT` | not set |
| `MANGO_WEBHOOK_SECRET` | not set |
| `MANGO_WEBHOOK_SHARED_SECRET` | not set |
| Admin auth for backend probe | configured |

The Mango client currently reads:

- `MANGO_API_KEY`
- `MANGO_API_SALT`
- `MANGO_API_BASE_URL`

The broader Mango runtime path also depends on:

- `MANGO_FROM_EXT`
- `MANGO_WEBHOOK_SECRET`
- `MANGO_WEBHOOK_SHARED_SECRET`

## Real Connectivity

DNS and TLS were verified successfully against `app.mango-office.ru`:

- DNS resolved to `81.88.85.67`
- TLS handshake succeeded
- certificate CN matched `*.mango-office.ru`

## Real Mango API Calls

### Direct call: `POST /incominglines`

- Status: `200`
- Success: yes
- Parsed lines: `2`

Confirmed live line data:

| remote_line_id | phone_number | schema_name |
|---|---|---|
| `405519147` | `+79585382099` | `По умолчанию` |
| `405622036` | `+79300350609` | `ДЛЯ ИИ менеджера` |

### Direct call: `POST /config/users/request`

- Status: `200`
- Success: yes
- Parsed extension/user records: `2`

Parseable live records included:

| extension | display_name | outgoing_line |
|---|---|---|
| `10` | redacted in repo docs | `+79585382099` |
| `12` | redacted in repo docs | `+79585382099` |

Important note:

- Mango returned a **nested payload** (`general` + `telephony`)
- the original parser did not recognize this tenant-specific shape
- the parser was updated to handle the nested structure

## Backend `sync-lines` Verification

### `POST /v1/telephony/mango/sync-lines`

- Status: `200`
- Success: yes
- Synced rows: `2`
- Deactivated rows: `0`

### What was written to `telephony_lines`

After the live sync and DB query, the stored lines were:

| provider | remote_line_id | phone_number | display_name | is_active |
|---|---|---|---|---|
| `mango` | `405622036` | `+79300350609` | `ДЛЯ ИИ менеджера` | `true` |
| `mango` | `405519147` | `+79585382099` | `По умолчанию` | `true` |

Confirmed facts:

- `phone_number` is now stored in normalized `+7...` form
- `provider_resource_id` is the stable remote Mango line ID
- `display_name` is correctly derived for the live AI line
- the sync path does not report false success on empty line inventory

Current limitation:

- `schema_name` is visible in the raw Mango payload and reflected into `display_name`
- it is **not materialized as a dedicated database column yet**

## Live Binding Check: Agent -> Mango Line

The probe created a temporary agent through the backend, bound it to the synced Mango line, read the settings back, and deleted the temporary agent.

### Binding target

- remote Mango line ID: `405622036`
- phone number: `+79300350609`
- label: `ДЛЯ ИИ менеджера`

### Verified roundtrip

- `POST /v1/agents` -> `201`
- `PATCH /v1/agent-profiles/{id}/settings` -> `200`
- `GET /v1/agent-profiles/{id}/settings` -> `200`

Confirmed on the returned payload:

- `telephony_provider = "mango"`
- `telephony_line_id` points to the local `telephony_lines.id`
- `telephony_line.provider_resource_id = "405622036"`
- `telephony_line.phone_number = "+79300350609"`
- `telephony_line.display_name = "ДЛЯ ИИ менеджера"`

This means the existing binding contract is live-confirmed and usable:

- local FK: `telephony_line_id`
- provider canonical key on the synced line row: `provider_resource_id` / remote Mango line ID

## UI Select Readiness

The current live inventory is already sufficient for an admin dropdown:

- stable provider key: `provider_resource_id`
- stable local row key: `telephony_lines.id`
- human-readable label available for the AI line: `ДЛЯ ИИ менеджера`
- normalized number available: `+79300350609`

Recommended canonical provider-side key for future routing logic:

- `remote_line_id = provider_resource_id`

Not recommended as the binding key:

- raw phone number alone
- label alone
- extension alone

## Routing Foundation Readiness

### Inbound number -> agent lookup

Partially ready.

What is already available:

- normalized inbound line phone number in `telephony_lines`
- stable remote Mango line ID
- agent -> line binding in the database

What is still missing:

- configured webhook secret
- live webhook registration in Mango
- live event payload confirmation for call lifecycle

### Outbound agent -> Mango line lookup

Partially ready.

What is already available:

- agent binding to a live synced Mango line
- line inventory with stable provider IDs

What is still missing:

- `MANGO_FROM_EXT`
- live originate verification
- production runtime switch from `stub` to Mango where appropriate

## What Had To Be Fixed

### 1. Extension parser mismatch for live tenant payload

Problem:

- `/config/users/request` returned real data
- the parser produced zero records because the tenant payload is nested under `general` and `telephony`

Fix:

- parse `general.name`
- parse `telephony.extension`
- parse `telephony.outgoingline`

### 2. Existing synced line rows were not guaranteed to refresh normalized phone numbers

Problem:

- the sync path did not explicitly overwrite `line.phone_number` for already existing rows

Fix:

- update `line.phone_number = remote.phone_number` on every sync cycle

Result:

- the live backend `sync-lines` response and the database now show normalized `+7...` numbers

## Rate Limit Observation

The Mango tenant/API applies a per-action limit for `vpbx/config/users/request`.

Observed live behavior:

- first direct request -> `200`
- repeated requests in the same probe window -> `429 Too Many Requests`
- backend `GET /v1/telephony/mango/extensions` in the same run -> `502` because Mango returned `429`

This is a real operational constraint, not a local bug.

Implication:

- extension inventory should not be aggressively polled
- UI should tolerate temporary extension unavailability
- line binding must remain usable even when extensions are unavailable

## What Is Confirmed vs Not Confirmed

### Confirmed

- Mango API connectivity
- signature/auth acceptance
- live line inventory
- live extension/user payload availability
- live backend sync to `telephony_lines`
- live agent binding roundtrip

### Not Confirmed

- inbound webhook delivery from Mango
- outbound originate
- full PSTN runtime
- media bridge
- production call routing end-to-end

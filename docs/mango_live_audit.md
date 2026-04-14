# Mango Live Audit

Date: 2026-04-14  
Last doc sync: 2026-04-13  
Method: Real env-backed Mango API calls, backend `sync-lines`, DB verification, agent binding roundtrip  
Tooling: `scripts/mango_live_probe.py`, `docs/mango_live_inventory_sample.json`

## Executive Summary

| Check | Result |
|---|---|
| Mango connectivity | YES |
| Auth / signature | YES |
| Live inventory fetched | YES |
| `sync-lines` live verified | YES |
| Agent binding on live Mango data | YES |
| `config/users/request` returns usable extensions for this tenant | NO |
| Repeated `config/users/request` calls stable | NO |

Current live-confirmed tenant state:

- `/incominglines` returns **2 real Mango lines**
- target AI line is:
  - `remote_line_id = 405622036`
  - `phone_number = +79300350609`
  - `schema_name = "ДЛЯ ИИ менеджера"`
- `/config/users/request` currently returns `users: []` for this tenant
- `MANGO_FROM_EXT` is not configured
- `MANGO_WEBHOOK_SECRET` is not configured

The important operational nuance is that line binding is already usable, but extensions and full PSTN routing are still partial:

- line inventory is live-confirmed and synced
- agent binding to a live Mango line is live-confirmed
- extensions are empty in the current tenant snapshot
- repeated `config/users/request` calls may return `429`

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

The Mango client reads:

- `MANGO_API_KEY`
- `MANGO_API_SALT`
- `MANGO_API_BASE_URL`

The future webhook/originate runtime still additionally depends on:

- `MANGO_FROM_EXT`
- `MANGO_WEBHOOK_SECRET`
- `MANGO_WEBHOOK_SHARED_SECRET`

## Real Connectivity

DNS and TLS were verified successfully against `app.mango-office.ru`:

- DNS resolved successfully
- TLS handshake succeeded
- real signed Mango requests were accepted

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
- Tenant result: `users: []`

This means:

- Mango connectivity is fine
- auth/signature is fine
- but this tenant does **not** currently expose extensions/users via this call in a way we can use for agent binding

## Backend `sync-lines` Verification

### `POST /v1/telephony/mango/sync-lines`

- Status: `200`
- Success: yes
- Synced rows: `2`
- Deactivated rows: `0`

### What is now written to `telephony_lines`

After live sync, the database stores:

| provider | remote_line_id | phone_number | schema_name | is_active |
|---|---|---|---|---|
| `mango` | `405622036` | `+79300350609` | `ДЛЯ ИИ менеджера` | `true` |
| `mango` | `405519147` | `+79585382099` | `По умолчанию` | `true` |

Confirmed facts:

- `phone_number` is stored in normalized `+7...` form
- `provider_resource_id` is the stable remote Mango line ID
- `schema_name` is now materialized as a dedicated DB column
- label generation can safely prefer `schema_name`
- the sync path does not silently succeed on empty line inventory

## Live Binding Check: Agent -> Mango Line

The binding roundtrip was verified on the real live-synced AI line.

### Binding target

- `remote_line_id = 405622036`
- `phone_number = +79300350609`
- `schema_name = ДЛЯ ИИ менеджера`

### Verified roundtrip

- `POST /v1/agents` -> `201`
- `PATCH /v1/agent-profiles/{id}/settings` -> `200`
- `GET /v1/agent-profiles/{id}/settings` -> `200`

Confirmed on the saved/read payload:

- `telephony_provider = "mango"`
- `telephony_remote_line_id = "405622036"`
- `telephony_line.phone_number = "+79300350609"`
- `telephony_line.schema_name = "ДЛЯ ИИ менеджера"`
- UI/runtime label resolves to `ДЛЯ ИИ менеджера (+79300350609)`

This is the current canonical binding contract:

- provider-side key: `telephony_remote_line_id`
- DB-side provider key: `provider_resource_id`
- local FK remains internal implementation detail

## UI Select Readiness

The current live inventory is sufficient for the admin dropdown.

What is available:

- stable provider key: `remote_line_id`
- normalized number: `+79300350609`
- human-readable label: `schema_name`
- active flag per line

Current UX behavior:

- line label prefers `schema_name`, then fallback label, then phone
- the AI line `ДЛЯ ИИ менеджера` is suggested first for unbound agents
- missing `MANGO_WEBHOOK_SECRET` and `MANGO_FROM_EXT` are shown as non-blocking warnings
- empty extensions do not block line binding
- temporary extensions rate limiting is surfaced as a warning, not a blocking failure
- admin debug APIs now cover both directions:
  - `POST /v1/telephony/mango/debug/resolve-inbound`
  - `GET /v1/telephony/mango/debug/resolve-outbound/{agent_id}`

## Routing Foundation Readiness

### Inbound number -> agent lookup

Partially ready.

Already available:

- normalized inbound line phone number in `telephony_lines`
- stable remote Mango line ID
- agent -> line binding in the database

Still missing:

- configured webhook secret
- live webhook registration in Mango
- confirmed live event payload contract on real calls

### Outbound agent -> Mango line lookup

Partially ready.

Already available:

- agent binding to a live synced Mango line
- stable line inventory with remote Mango IDs

Still missing:

- `MANGO_FROM_EXT`
- live originate verification
- end-to-end PSTN runtime confirmation on real calls

Implementation note:

- Mango originate path can now accept an explicit agent-bound `telephony_remote_line_id`
- this prepares the control plane for agent-specific `line_number` in `/commands/callback`
- live confirmation still requires a real outbound call after `MANGO_FROM_EXT` is set

## Rate Limit Observation

The Mango tenant/API applies a per-action limit for `vpbx/config/users/request`.

Observed live behavior:

- first request can return `200`
- repeated requests in the same probe window may return `429 Too Many Requests`

Implication:

- extension inventory should not be aggressively polled
- line binding must remain usable even when extensions are temporarily unavailable
- admin UX should treat extensions failures as non-blocking unless line inventory itself is broken

## What Is Confirmed vs Not Confirmed

### Confirmed

- Mango API connectivity
- signature/auth acceptance
- live line inventory
- live backend sync to `telephony_lines`
- normalized line storage in DB
- dedicated `schema_name` storage
- live agent binding roundtrip
- admin-side binding contract around `telephony_remote_line_id`

### Not Confirmed

- live inbound webhook event from Mango after the latest admin-side polish
- live outbound originate using agent-bound Mango line
- live inbound number -> agent routing on a real phone call
- stable extensions inventory for this tenant
- full PSTN runtime

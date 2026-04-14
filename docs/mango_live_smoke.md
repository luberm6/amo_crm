# Mango Live Smoke

This document covers the next practical step after Mango inventory sync and
agent binding:

- webhook smoke against the configured backend URL
- outbound originate smoke from an agent-bound Mango line

It does **not** claim that full PSTN media is already proven.

## What This Step Proves

- Mango inventory and agent binding exist in the control plane
- backend can evaluate webhook readiness honestly
- backend can resolve which agent should handle an inbound Mango number
- backend can resolve which Mango line and extension would be used for outbound originate
- a safe operator script exists for webhook smoke
- a safe dry-run/live script exists for outbound originate smoke

## What This Step Does Not Prove

- real Mango webhook delivery from the tenant
- real inbound Mango call -> AI audio runtime
- full PSTN media path
- real outbound callback/originate unless you run the live smoke with a destination number

## Scripts

### Webhook smoke

```bash
.venv/bin/python scripts/mango_webhook_smoke.py --to-number +79300350609
```

What it does:

- builds a Mango-like inbound webhook payload
- signs it with `MANGO_WEBHOOK_SECRET` or uses `MANGO_WEBHOOK_SHARED_SECRET`
- sends it to `${BACKEND_URL}/v1/webhooks/mango`
- prints the structured backend response

Important:

- if `BACKEND_URL` is local/private, the script will warn that real tenant-side
  webhook delivery is still blocked
- this is a smoke against the backend endpoint, not proof of live Mango delivery

### Outbound originate smoke

Dry-run:

```bash
.venv/bin/python scripts/mango_originate_smoke.py
```

Live originate:

```bash
.venv/bin/python scripts/mango_originate_smoke.py --live --to +7XXXXXXXXXX
```

What it does:

- resolves the most recent active Mango-bound agent unless `--agent-id` is passed
- resolves the agent-bound Mango line
- resolves `from_ext` from explicit config or Mango auto-discovery
- in live mode, performs real `POST /commands/callback` through `MangoTelephonyAdapter`

## Honest Readiness Interpretation

Use `GET /v1/telephony/mango/readiness` and the Providers page to separate:

- control-plane readiness
- webhook smoke readiness
- outbound originate smoke readiness
- inbound AI runtime readiness

If `BACKEND_URL` is not public or webhook secret is missing, webhook smoke is
blocked even if inventory sync and line binding already work.

# Providers Settings

## Purpose

`Providers Settings` is a safe admin settings layer for external integrations:

- Mango
- Gemini
- ElevenLabs
- Vapi

This layer is intentionally separate from phone-number routing and number assignment.

## What It Supports

The admin panel can now:

- save provider credentials/settings
- keep secrets encrypted at rest
- return only masked secrets in API responses
- validate settings explicitly through a `Check connection` action
- mark settings `active/inactive` as operator intent

## What It Does Not Do Yet

Very important:

- it does **not** auto-connect a shared Mango account to AI routing
- it does **not** sync Mango numbers
- it does **not** assign a number to an agent
- it does **not** take over numbers already used by amoCRM
- it does **not** automatically switch the live runtime to these stored settings

This is a settings layer only.

## Supported Providers

### Mango

Stored fields:

- `api_key`
- `api_salt`
- `webhook_secret`
- `webhook_shared_secret`
- `from_ext`
- `webhook_ip_allowlist`

Validation behavior:

- safe-mode validation only
- checks that required credential material is present
- does **not** call number sync
- does **not** place a call
- does **not** change routing

### Gemini

Stored fields:

- `api_key`
- `model_id`
- `api_version`

Validation behavior:

- remote read-only model check

### ElevenLabs

Stored fields:

- `api_key`
- `voice_id`
- `enabled`

Validation behavior:

- remote read-only voice check

### Vapi

Stored fields:

- `api_key`
- `webhook_secret`
- `assistant_id`
- `phone_number_id`
- `base_url`
- `server_url`

Validation behavior:

- remote read-only assistant check

## API

- `GET /v1/providers/settings`
- `PATCH /v1/providers/settings/{provider}`
- `POST /v1/providers/settings/{provider}/validate`

All endpoints require admin bearer auth.

## Secret Handling

Secrets are:

- encrypted before storage
- never returned in raw form
- masked in API responses and UI
- not logged intentionally by the providers settings layer

Encryption secret source:

- `PROVIDER_SETTINGS_SECRET`
- fallback: `ADMIN_AUTH_SECRET`

Recommended: set a dedicated `PROVIDER_SETTINGS_SECRET`.

## UI Behavior

Each provider card shows:

- current config status: `configured` / `invalid` / `not_tested`
- activation status: `active` / `inactive`
- masked secret indicators
- last validation result
- explicit safe-mode note

## Manual QA Checklist

1. Open `Providers` in the admin panel.
2. Save Mango credentials.
3. Confirm the response/UI never shows raw secrets after save.
4. Validate Mango settings.
5. Confirm the UI message explicitly says no routing or number sync was triggered.
6. Save Gemini or ElevenLabs settings.
7. Run `Check connection`.
8. Confirm status changes to `configured` only after explicit validation.
9. Refresh the page.
10. Confirm masked secrets are still shown as stored and inputs stay blank.

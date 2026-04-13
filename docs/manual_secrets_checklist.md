# Manual Secrets Checklist

Only the following values still need to be inserted manually.

Local infra note:

- local Postgres is expected on `127.0.0.1:5433`
- this is intentional and avoids collisions with a system Postgres on `5432`

## Required For Local Admin Login

| Variable | File | Required | Unlocks | Where to get |
|---|---|---:|---|---|
| `ADMIN_EMAIL` | `.env` | Yes | Admin login | Set to any email you want to use locally |
| `ADMIN_PASSWORD` | `.env` | Yes | Admin login | Set to any password you want to use locally |
| `ADMIN_AUTH_SECRET` | `.env` | Yes | Admin session tokens | Any random string ≥ 32 chars, e.g. `openssl rand -hex 32` |
| `PROVIDER_SETTINGS_SECRET` | `.env` | Recommended | Secure provider secret storage | Any random string ≥ 32 chars, e.g. `openssl rand -hex 32` |

Local templates already include safe dev defaults for these, so you do **not**
have to replace them unless you want different local credentials.

## Required For Browser Voice

| Variable | File | Required | Unlocks | Where to get |
|---|---|---:|---|---|
| `GEMINI_API_KEY` | `.env` | Yes | Browser voice input + model responses | [Google AI Studio](https://aistudio.google.com) → Get API key |
| `ELEVENLABS_API_KEY` | `.env` | Yes for `tts_primary` | Browser voice playback via TTS | [ElevenLabs](https://elevenlabs.io) → Profile → API Keys |
| `ELEVENLABS_VOICE_ID` | `.env` | Yes for `tts_primary` | Browser voice playback via TTS | ElevenLabs → Voice Library → pick a voice → copy Voice ID |

After inserting these values, run:

```bash
make doctor-local
```

Doctor should move browser voice from blocked/partial toward ready.

## Optional For Real PSTN / Mango / FreeSWITCH

These are **not** required for localhost admin or browser sandbox UI.

| Variable | File | Required | Unlocks | Where to get |
|---|---|---:|---|---|
| `MANGO_API_BASE_URL` | `.env` | Optional | Mango API sync against a tenant-specific endpoint | Use the official default `https://app.mango-office.ru/vpbx` unless Mango support gave you a different API base URL |
| `MANGO_API_KEY` | `.env` | Optional | Real Mango telephony | [Mango VPBX](https://app.mango-office.ru/vpbx/) → Settings → API |
| `MANGO_API_SALT` | `.env` | Optional | Real Mango telephony | Same location as `MANGO_API_KEY` (shown together) |
| `MANGO_FROM_EXT` | `.env` | Optional | Mango originate/callback source | Mango VPBX → Extensions → your extension number |
| `MANGO_WEBHOOK_SHARED_SECRET` | `.env` | Optional | Mango webhook verification | Any random string you set; also configure the same value in Mango → Webhooks settings |
| `FREESWITCH_ESL_PASSWORD` | `.env` | Optional | Real FreeSWITCH media gateway | FreeSWITCH config → `autoload_configs/event_socket.conf.xml` → `password` field |
| `VAPI_API_KEY` | `.env` | Optional | Vapi provider settings/route | [Vapi dashboard](https://app.vapi.ai) → Account → API Keys |
| `TELEGRAM_BOT_TOKEN` | `.env` | Optional | Telegram bot integration | Telegram → [@BotFather](https://t.me/BotFather) → `/newbot` |

Important:

- adding Mango credentials does **not** automatically enable AI number routing
- provider settings are still only a settings layer, not a number takeover layer
- FreeSWITCH is not required for browser sandbox testing — only for real PSTN Direct voice

## Quick Secret Generation

Generate a secure random secret locally:

```bash
openssl rand -hex 32
```

Use the output for `ADMIN_AUTH_SECRET` and `PROVIDER_SETTINGS_SECRET`.

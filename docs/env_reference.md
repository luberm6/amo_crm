# Environment Reference

This document lists the current env variables used by the project.

Legend for `Required`:

- `Yes`: needed for the corresponding component/use case
- `No`: optional
- `Contextual`: required only for a specific route or runtime mode

## Core Backend

| Variable | Required | Default | Used by | Purpose | Blocks what if missing |
|---|---|---|---|---|---|
| `DATABASE_URL` | Yes | `postgresql+asyncpg://amo_user:amo_pass@127.0.0.1:5433/amo_crm` | backend, migrations, tests | Primary database connection | Backend startup, migrations, admin/runtime persistence |
| `REDIS_URL` | Yes | `redis://localhost:6379/0` | backend, Celery, rate limiting | Redis broker/cache/session coordination | Startup, readiness, worker/beat, some runtime coordination |
| `BACKEND_URL` | No | `http://127.0.0.1:8000` | bot, docs, callbacks | Canonical backend URL | External callback correctness; not core localhost backend start |
| `ADMIN_CORS_ORIGINS` | Contextual | empty | backend CORS middleware | Comma-separated production browser origins for admin panel / browser sandbox | Cross-origin admin panel and browser sandbox requests in production |
| `ENVIRONMENT` | Yes | `development` | backend, tests | Runtime mode: `development`, `production`, `testing` | Wrong mode changes validation behavior |
| `LOG_LEVEL` | No | `INFO` | backend | Log verbosity | Does not block start |
| `LOG_FORMAT` | No | `console` | backend | `console` or `json` logging | Does not block start |
| `DEFAULT_PHONE_COUNTRY` | No | `RU` | backend | Phone normalization fallback | Phone parsing quality only |
| `METRICS_ENABLED` | No | `true` | backend | Metrics endpoint/exporters | Metrics only |

## Security / Admin

| Variable | Required | Default | Used by | Purpose | Blocks what if missing |
|---|---|---|---|---|---|
| `API_KEY` | No | empty | backend API | Optional `X-API-Key` auth for mutating public API endpoints | Only API-key enforcement |
| `ADMIN_EMAIL` | Yes for admin login | empty in code, local template provides `admin@example.com` | admin auth | Admin username/email | Admin panel login |
| `ADMIN_PASSWORD` | Yes for admin login | empty in code, local template provides `admin12345` | admin auth | Admin password | Admin panel login |
| `ADMIN_AUTH_SECRET` | Yes for admin login | empty in code, local template provides dev value | admin auth | Signs admin bearer tokens | Admin panel login |
| `PROVIDER_SETTINGS_SECRET` | Recommended | empty in code, local template provides dev value | providers settings | Encrypts provider secrets at rest | Safe secret storage in Providers UI |
| `ADMIN_TOKEN_TTL_SECONDS` | No | `28800` | admin auth | Admin token lifetime | Only admin token expiry behavior |

## Telegram Bot

| Variable | Required | Default | Used by | Purpose | Blocks what if missing |
|---|---|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | No for browser-first local dev | empty | Telegram bot | Bot authentication | Telegram bot only |

## Rate Limiting

| Variable | Required | Default | Used by | Purpose | Blocks what if missing |
|---|---|---|---|---|---|
| `RATE_LIMIT_ENABLED` | No | `true` | backend | Master switch for rate limiting | Does not block startup |
| `RATE_LIMIT_GLOBAL_PER_IP_PER_MINUTE` | No | `60` | backend | Global IP-based rate cap | Rate limiting only |
| `RATE_LIMIT_CALLS_PER_MINUTE` | No | `10` | backend | Outbound call create cap | Rate limiting only |
| `RATE_LIMIT_STEER_PER_CALL_PER_MINUTE` | No | `20` | backend | Steering rate cap | Rate limiting only |
| `RATE_LIMIT_TRANSFER_PER_CALL` | No | `3` | backend | Transfer attempts per call | Transfer throttling only |
| `RATE_LIMIT_TRANSFER_COOLDOWN_SECONDS` | No | `60` | backend | Transfer cooldown | Transfer throttling only |
| `RATE_LIMIT_CALLS_PER_PHONE_PER_DAY` | No | `5` | backend | Daily phone cap | Rate limiting only |
| `RATE_LIMIT_MAX_CONCURRENT_CALLS` | No | `50` | backend | Max active calls | Rate limiting only |
| `RATE_LIMIT_PHONE_REPEAT_COOLDOWN_SECONDS` | No | `300` | backend | Retry cooldown per phone | Rate limiting only |

## Quiet Hours

| Variable | Required | Default | Used by | Purpose | Blocks what if missing |
|---|---|---|---|---|---|
| `ENFORCE_QUIET_HOURS` | No in dev, strongly expected in prod | `false` | backend | Enable calling window enforcement | Calling policy only |
| `CALLING_HOUR_START` | No | `9` | backend | Start of call window | Calling policy only |
| `CALLING_HOUR_END` | No | `21` | backend | End of call window | Calling policy only |
| `CALLING_TIMEZONE` | No | `Europe/Moscow` | backend | Quiet hours timezone | Calling policy only |

## Telephony Provider Selection

| Variable | Required | Default | Used by | Purpose | Blocks what if missing |
|---|---|---|---|---|---|
| `TELEPHONY_PROVIDER` | Contextual | `auto` in code, `stub` in local template | router/runtime | Selects telephony provider route | Real PSTN only; browser sandbox can run with `stub` |

## Mango Telephony

| Variable | Required | Default | Used by | Purpose | Blocks what if missing |
|---|---|---|---|---|---|
| `MANGO_API_BASE_URL` | No | `https://app.mango-office.ru/vpbx` | Mango provider, admin telephony sync | Mango API base URL for line sync/originate/webhooks | Only custom Mango endpoint override |
| `MANGO_API_KEY` | Contextual | empty | Mango provider, preflight | Mango API credential | Real Mango telephony |
| `MANGO_API_SALT` | Contextual | empty | Mango provider, preflight | Mango API signing salt | Real Mango telephony |
| `MANGO_FROM_EXT` | Contextual | empty | Mango telephony | Click-to-call source extension/line | Real Mango originate/callback source |
| `MANGO_WEBHOOK_SECRET` | No | empty | Mango webhook validation | Native webhook signature secret | Webhook hardening only |
| `MANGO_WEBHOOK_SHARED_SECRET` | No | empty | Mango webhook validation | Shared-secret fallback verifier | Webhook hardening only |
| `MANGO_WEBHOOK_IP_ALLOWLIST` | No | empty | Mango webhook validation | IP allowlist | Webhook hardening only |
| `MANGO_ANSWER_WAIT_TIMEOUT_SECONDS` | No | `30` | Mango runtime | Wait for answer timeout | Mango timing only |
| `MANGO_BRIDGE_CONFIRM_TIMEOUT_SECONDS` | No | `12` | Mango runtime | Bridge confirmation timeout | Mango timing only |
| `MANGO_WHISPER_CONFIRM_TIMEOUT_SECONDS` | No | `15` | Mango runtime | Whisper confirmation timeout | Mango timing only |

## Media Gateway / FreeSWITCH

| Variable | Required | Default | Used by | Purpose | Blocks what if missing |
|---|---|---|---|---|---|
| `MEDIA_GATEWAY_ENABLED` | Contextual | `false` | Direct voice RTP path | Enables media gateway integration | Real PSTN Direct voice |
| `MEDIA_GATEWAY_MODE` | Contextual | `disabled` | Direct voice RTP path | `disabled`, `mock`, `scaffold`, `esl_rtp` | Real RTP path if wrong/missing |
| `MEDIA_GATEWAY_PROVIDER` | No | `freeswitch` | media gateway factory | Provider selector | Media path selection only |
| `FREESWITCH_ESL_HOST` | Contextual | `127.0.0.1` | FreeSWITCH gateway | ESL host | Real FreeSWITCH integration |
| `FREESWITCH_ESL_PORT` | Contextual | `8021` | FreeSWITCH gateway | ESL port | Real FreeSWITCH integration |
| `FREESWITCH_ESL_PASSWORD` | Contextual | `ClueCon` in dev template | FreeSWITCH gateway | ESL password | Real FreeSWITCH integration |
| `FREESWITCH_SIP_PROFILE` | No | `external` | FreeSWITCH gateway | SIP profile | PSTN/media behavior only |
| `FREESWITCH_SIP_DOMAIN` | No | `localhost` | FreeSWITCH gateway | SIP domain | PSTN/media behavior only |
| `FREESWITCH_RTP_IP` | Contextual | `127.0.0.1` | FreeSWITCH gateway | RTP bind IP | Real media path |
| `FREESWITCH_RTP_PORT_START` | No | `16384` | FreeSWITCH gateway | RTP start port | Media path only |
| `FREESWITCH_RTP_PORT_END` | No | `32768` | FreeSWITCH gateway | RTP end port | Media path only |
| `FREESWITCH_SESSION_TIMEOUT_SECONDS` | No | `120` | FreeSWITCH gateway | Session timeout | Media path only |
| `FREESWITCH_RTP_PAYLOAD_TYPE` | No | `96` | FreeSWITCH gateway | RTP payload type | Media path only |
| `FREESWITCH_ATTACH_COMMAND_TEMPLATE` | No | `uuid_media_reneg {uuid} ={rtp_ip}:{rtp_port}` | FreeSWITCH gateway | Attach command template | Media attach behavior |
| `FREESWITCH_HANGUP_COMMAND_TEMPLATE` | No | `uuid_kill {uuid}` | FreeSWITCH gateway | Hangup command template | Media cleanup only |
| `FREESWITCH_ESL_EVENTS` | No | `CHANNEL_HANGUP_COMPLETE CUSTOM HEARTBEAT` | FreeSWITCH gateway | ESL subscriptions | Media signaling visibility |
| `FREESWITCH_ESL_CONNECT_TIMEOUT_SECONDS` | No | `5.0` | FreeSWITCH gateway | ESL connect timeout | Media connection timing |
| `FREESWITCH_ESL_RECONNECT_ENABLED` | No | `true` | FreeSWITCH gateway | Reconnect toggle | Gateway resilience only |
| `FREESWITCH_ESL_RECONNECT_INITIAL_DELAY_SECONDS` | No | `0.5` | FreeSWITCH gateway | Initial reconnect delay | Gateway resilience only |
| `FREESWITCH_ESL_RECONNECT_MAX_DELAY_SECONDS` | No | `5.0` | FreeSWITCH gateway | Max reconnect delay | Gateway resilience only |
| `FREESWITCH_ESL_RECONNECT_MAX_ATTEMPTS` | No | `0` | FreeSWITCH gateway | Max reconnect attempts | Gateway resilience only |
| `FREESWITCH_RTP_INBOUND_CODEC` | No | `pcm16` | FreeSWITCH gateway | Expected inbound codec | Media decoding only |
| `FREESWITCH_RTP_OUTBOUND_CODEC` | No | `pcm16` | FreeSWITCH gateway | Outbound codec | Media encoding only |
| `FREESWITCH_RTP_SAMPLE_RATE_HZ` | No | `16000` | FreeSWITCH gateway | RTP sample rate | Media format only |
| `FREESWITCH_RTP_FRAME_BYTES` | No | `640` | FreeSWITCH gateway | RTP frame size | Media buffering only |
| `FREESWITCH_RTP_INBOUND_TIMEOUT_SECONDS` | No | `15` | FreeSWITCH gateway | No-RTP timeout | Media fault handling |
| `FREESWITCH_RTP_OUTBOUND_BUFFER_MAX_FRAMES` | No | `50` | FreeSWITCH gateway | Initial outbound buffer | First-turn media behavior only |
| `FREESWITCH_EVENT_QUEUE_MAX` | No | `512` | FreeSWITCH gateway | ESL event queue limit | Gateway buffering only |

## Vapi

| Variable | Required | Default | Used by | Purpose | Blocks what if missing |
|---|---|---|---|---|---|
| `VAPI_API_KEY` | Contextual | empty | Vapi integration, providers UI | Vapi API access | Vapi route/settings only |
| `VAPI_ASSISTANT_ID` | Contextual | empty | Vapi integration | Assistant selection | Vapi route only |
| `VAPI_PHONE_NUMBER_ID` | Contextual | empty | Vapi integration | Outbound number selection | Vapi route only |
| `VAPI_SERVER_URL` | Contextual | empty | Vapi webhook setup | Public webhook base URL | Vapi webhook delivery |
| `VAPI_WEBHOOK_SECRET` | No | empty | Vapi webhook validation | Signature validation | Webhook hardening only |
| `VAPI_BASE_URL` | No | `https://api.vapi.ai` | Vapi integration | REST API base URL | Vapi connectivity only |

## Transfers

| Variable | Required | Default | Used by | Purpose | Blocks what if missing |
|---|---|---|---|---|---|
| `TRANSFER_MANAGER_ANSWER_TIMEOUT` | No | `30` | transfer runtime | Manager answer timeout | Transfer timing only |
| `TRANSFER_BRIEFING_TIMEOUT` | No | `15` | transfer runtime | Whisper/briefing timeout | Transfer timing only |
| `TRANSFER_BRIDGE_TIMEOUT` | No | `10` | transfer runtime | Bridge timeout | Transfer timing only |
| `TRANSFER_MAX_MANAGER_ATTEMPTS` | No | `3` | transfer runtime | Max manager attempts | Transfer behavior only |
| `TRANSFER_MANAGER_COOLDOWN_SECONDS` | No | `300` | transfer runtime | Cooldown for unavailable managers | Transfer behavior only |
| `TRANSFER_MANAGER_RESTORE_ENABLED` | No | `true` | transfer runtime | Restore loop toggle | Restore loop only |
| `TRANSFER_MANAGER_RESTORE_INTERVAL_SECONDS` | No | `30` | transfer runtime | Restore loop interval | Restore loop only |

## Direct Runtime / Gemini

| Variable | Required | Default | Used by | Purpose | Blocks what if missing |
|---|---|---|---|---|---|
| `GEMINI_API_KEY` | Contextual | empty | Direct runtime, preflight, Browser Call | Gemini API access | Browser voice / Direct runtime responses |
| `GEMINI_MODEL_ID` | No | `gemini-2.0-flash-live-001` | Direct runtime | Gemini model selection | Model selection only |
| `GEMINI_API_VERSION` | No | `v1beta` | Direct runtime | Gemini API version | Gemini connection only |
| `GEMINI_SYSTEM_PROMPT` | No | built-in Russian sales prompt | Direct runtime fallback | Default system prompt when no agent profile is supplied | Prompt quality only |
| `GEMINI_SETUP_TIMEOUT` | No | `5.0` | Direct runtime | Setup timeout | Runtime timing only |
| `DIRECT_MAX_SESSIONS` | No | `10` | Direct runtime | In-process session cap | Concurrency cap only |
| `DIRECT_VOICE_STRATEGY` | Contextual | `disabled` in code, `tts_primary` in local template | Direct runtime, Browser Call | Primary/fallback voice policy | Browser voice / Direct voice if invalid |
| `DIRECT_VOICE_ALLOW_TTS_FALLBACK` | No | `true` | Direct runtime | Allow TTS fallback from Gemini primary | Fallback policy only |
| `GEMINI_AUDIO_OUTPUT_ENABLED` | Contextual | `false` | Direct runtime | Enable Gemini native audio output | Required for `gemini_primary` |
| `GEMINI_AUDIO_INPUT_ENABLED` | No | `true` | Direct runtime | Send inbound audio to Gemini | Voice input only |
| `DIRECT_INITIAL_GREETING_ENABLED` | No | `true` | Direct runtime | Auto-start greeting | First-turn only |
| `DIRECT_INITIAL_GREETING_TEXT` | No | default greeting | Direct runtime | Greeting text | First-turn text only |
| `DIRECT_MODEL_RESPONSE_TIMEOUT_SECONDS` | No | `8.0` | Direct runtime | Fail-fast timeout for no model response | Runtime failure behavior only |
| `SUMMARY_LLM_PROVIDER` | No | empty | summaries/whispers | Optional summary provider | Summary enrichment only |
| `AUDIO_DEBUG_DUMP_ENABLED` | No | `false` | Direct runtime, Browser Call debug | Writes PCM debug WAV artifacts to disk for format investigation | Nothing functional; diagnostics only |
| `AUDIO_DEBUG_DUMP_DIR` | No | `/tmp/amo_crm_audio_debug` | Direct runtime, Browser Call debug | Target directory for PCM debug WAV artifacts | Nothing functional; diagnostics only |

## ElevenLabs

| Variable | Required | Default | Used by | Purpose | Blocks what if missing |
|---|---|---|---|---|---|
| `ELEVENLABS_API_KEY` | Contextual | empty | voice provider, Browser Call | ElevenLabs API access | `tts_primary` playback / TTS fallback |
| `ELEVENLABS_VOICE_ID` | Contextual | empty | voice provider, Browser Call | Voice selection | `tts_primary` playback / TTS fallback |
| `ELEVENLABS_ENABLED` | Contextual | `false` in code, `true` in local template | voice provider | Enables ElevenLabs voice path | `tts_primary` if disabled |

## Frontend

| Variable | Required | Default | Used by | Purpose | Blocks what if missing |
|---|---|---|---|---|---|
| `VITE_API_BASE_URL` | No | empty | admin panel | Override backend base URL; empty uses local Vite proxy | Frontend API routing only |

## Test-only

| Variable | Required | Default | Used by | Purpose | Blocks what if missing |
|---|---|---|---|---|---|
| `TEST_DATABASE_URL` | No | `sqlite+aiosqlite:///:memory:` | pytest | Optional Postgres test DB override | Postgres-specific tests only |

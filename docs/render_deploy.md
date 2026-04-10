# Render Deploy

This document describes the current minimal Render deployment for the project.

Scope of this deployment:

- FastAPI backend
- React admin panel
- managed PostgreSQL
- managed Redis
- browser sandbox QA

This is not a PSTN/Mango deployment by default.

## Honest Status

- backend blueprint: prepared
- admin static site: prepared
- Render logs / startup diagnostics: improved
- live Render deploy: not verified in this repository session

## Recommended Render Topology

Use the checked-in [render.yaml](/Users/iluxa/Amo_crm/render.yaml) blueprint.

It now defines:

- `amo-crm-api` — Python web service
- `amo-crm-admin` — static site for the React admin panel
- `amo-crm-db` — managed PostgreSQL
- `amo-crm-cache` — managed Redis

This is the recommended minimum for browser-based QA.

## Why Static Site For Admin

`admin-panel` is a Vite/React SPA with no SSR requirement.

Static Site is the simplest and safest option on Render because:

- build is straightforward: `npm install && npm run build`
- no Node server is required at runtime
- the app only needs `VITE_API_BASE_URL`
- browser WebSocket/audio calls still go to the backend directly

## Before You Push

1. Confirm `.env` is ignored.
2. Confirm `admin-panel/.env.local` is ignored.
3. Confirm `.venv/` and `node_modules/` are ignored.
4. Run:

```bash
python3 -m pytest -q tests/test_env_config.py tests/test_provider_settings_api.py tests/test_admin_auth.py
cd admin-panel && npm test && npm run build
```

## Create GitHub Repo

1. Create a new GitHub repository.
2. Push this project.
3. Confirm the repo does not contain local secrets.

## Create Render Blueprint

1. In Render, choose `New +`.
2. Select `Blueprint`.
3. Connect the GitHub repo.
4. Render will detect [render.yaml](/Users/iluxa/Amo_crm/render.yaml).
5. Review the generated services.
6. Create the stack.

## Backend Service Settings

The backend service uses:

- build command: `pip install -e .`
- pre-deploy command: `alembic upgrade head`
- start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

Important:

- `DATABASE_URL` comes from the Render Postgres connection string
- the backend now normalizes Render-style `postgres://...` or `postgresql://...` URLs to `postgresql+asyncpg://...`
- `REDIS_URL` comes from the Render Redis service

## Admin Static Site Settings

The admin site uses:

- root dir: `admin-panel`
- build command: `npm install && npm run build`
- publish dir: `dist`

You must set:

- `VITE_API_BASE_URL=https://<your-backend>.onrender.com`

The static site also rewrites all SPA paths to `/index.html`.

## Required Render Dashboard Env

For backend boot:

- `BACKEND_URL=https://<your-backend>.onrender.com`
- `ADMIN_EMAIL=...`
- `ADMIN_PASSWORD=...`
- `ADMIN_AUTH_SECRET=...`
- `PROVIDER_SETTINGS_SECRET=...`
- `ADMIN_CORS_ORIGINS=https://<your-admin>.onrender.com`

For browser voice:

- `GEMINI_API_KEY=...`
- `ELEVENLABS_API_KEY=...`
- `ELEVENLABS_VOICE_ID=...`

Recommended browser-sandbox runtime defaults:

- `TELEPHONY_PROVIDER=stub`
- `DIRECT_VOICE_STRATEGY=tts_primary`
- `GEMINI_AUDIO_INPUT_ENABLED=true`
- `GEMINI_AUDIO_OUTPUT_ENABLED=false`
- `ELEVENLABS_ENABLED=true`

## Where To Look In Render Logs

Backend logs:

- startup logs from `app.main`
- generic unhandled 500 logs:
  - `unhandled_exception`
- provider settings failures:
  - `provider_settings.save_failed`
  - `provider_settings.validate_failed`
- browser call events:
  - `browser_call.created`
  - `browser_call.debug_test_tone_sent`
  - `browser_call.debug_test_tts_failed`
- Direct runtime failures:
  - `session_manager.session_failed`
  - `direct_engine.initiate_call_failed`

## What Should Work After Deploy

With only backend/admin auth secrets:

- backend `/health`
- backend `/ready`
- admin login
- agents CRUD
- knowledge base CRUD
- providers settings CRUD

With Gemini + ElevenLabs secrets added:

- browser sandbox session creation
- provider validation
- browser voice path prerequisites

Still not automatically enabled:

- Mango telephony
- number sync
- PSTN routing

## Migration Strategy

Current strategy:

- `preDeployCommand: alembic upgrade head`

Why:

- keeps schema upgrade coupled to deploy
- avoids starting the web process against an old schema
- is simpler than a separate one-off migration service for this stage

## Honest Deployment Limits

- I did not perform a real Render deploy in this session
- static admin hosting is prepared, not live-verified
- browser audio on Render still requires manual validation in a real browser
- Mango/PSTN is not part of the minimal Render browser-sandbox deployment

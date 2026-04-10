# Local Setup

This project can now be bootstrapped locally with one main entry point:

```bash
make bootstrap-local
```

That bootstrap path is designed for the current internal workflow:

- FastAPI backend
- React admin panel
- local PostgreSQL
- local Redis
- admin login
- agent/knowledge/providers management
- browser sandbox UI

It is intentionally optimized for **localhost admin + browser QA**, not for full
PSTN telephony.

## What Bootstrap Automates

`make bootstrap-local` or `./scripts/bootstrap_local.sh` will:

- create `.venv` if it does not exist
- install backend dependencies into `.venv`
- copy `.env.local.example` to `.env` if `.env` is missing
- copy `admin-panel/.env.example` to `admin-panel/.env.local` if missing
- install `admin-panel` dependencies
- start local `postgres` and `redis` via the existing `docker-compose.yml` when Docker Compose is available
- run Alembic migrations
- run the local doctor and print the final state

## What Bootstrap Does Not Automate

Bootstrap does **not** and should **not** try to:

- create external provider accounts
- fetch real API keys
- enable Mango routing automatically
- sync Mango numbers
- configure FreeSWITCH for you
- prove browser audio is audible on your machine

Those are still manual or environment-specific steps.

## Prerequisites

Minimum local prerequisites:

- Python 3.9+
- Node.js
- npm
- Docker Compose recommended

If Docker Compose is unavailable, you can still run locally, but **Postgres and
Redis must already be running on localhost**.

For this project we intentionally keep local Postgres off the default macOS
`5432` port to avoid conflicts with a system Postgres already listening there.

Expected default local ports:

- backend: `8000`
- admin panel: `5173`
- postgres: `127.0.0.1:5433`
- redis: `6379`

## Files Created By Bootstrap

If they do not already exist, bootstrap creates:

- `.env`
- `admin-panel/.env.local`

Both are created from checked-in templates and are safe to edit locally.

## Recommended Local Scope

For the first local run, focus on:

- login to admin panel
- create/edit agent
- create knowledge documents
- bind knowledge to agent
- configure providers safely
- run Browser Call UI

Real PSTN routing remains a separate later step.

## Local Browser Voice Notes

The browser sandbox UI can run without real voice secrets.

However, actual browser voice is blocked until you provide the required secrets
for the selected voice strategy.

Recommended local strategy:

- `DIRECT_VOICE_STRATEGY=tts_primary`

Required for that strategy:

- `GEMINI_API_KEY`
- `ELEVENLABS_API_KEY`
- `ELEVENLABS_VOICE_ID`

Without those values the backend can still start, the admin panel can still be
used, and Browser Call UI can still open, but **voice will remain blocked**.

## Doctor Command

Run:

```bash
make doctor-local
```

or:

```bash
./.venv/bin/python scripts/local_env_doctor.py
```

Doctor expects local development Postgres on:

- `127.0.0.1:5433`

If your `.env` still points to `localhost:5432`, doctor will mark database setup
as blocked instead of silently connecting to the wrong Postgres.

Doctor returns one of:

- `READY`
- `PARTIAL`
- `BLOCKED`

Meaning:

- `READY`: localhost stack is configured and browser voice prerequisites are present
- `PARTIAL`: localhost stack is usable, but one or more optional or voice-specific prerequisites are missing
- `BLOCKED`: core local setup is not usable yet

# Local Runbook

## 1. Bootstrap once

```bash
make bootstrap-local
```

Local DB contract for this project:

- Postgres: `127.0.0.1:5433`
- Redis: `127.0.0.1:6379`

This avoids collisions with a system Postgres often already bound to `5432` on macOS.

## 2. Insert only the secrets you actually need

For browser voice, edit `.env` and set:

- `GEMINI_API_KEY`
- `ELEVENLABS_API_KEY`
- `ELEVENLABS_VOICE_ID`

If you only want admin UI without voice, you can skip those for now.

## 3. Re-run doctor

```bash
make doctor-local
```

Read the result honestly:

- `READY` means local stack is configured
- `PARTIAL` usually means UI is ready but browser voice or PSTN secrets are missing
- `BLOCKED` means core startup is still broken

If doctor says your database still points to `localhost:5432`, update `.env`
back to the canonical local URL from `.env.local.example`.

## 4. Start the services you need

Backend:

```bash
make run-backend
```

Admin panel:

```bash
make run-admin
```

Optional worker:

```bash
make run-worker
```

Optional beat:

```bash
make run-beat
```

Or start the local stack together:

```bash
make run-all
```

## 5. Open the admin panel

Open:

- `http://localhost:5173`

Default local login from `.env.local.example`:

- email: `admin@example.com`
- password: `admin12345`

## 6. Verify the minimum operator flow

1. Login
2. Create or edit an agent
3. Add knowledge documents
4. Bind knowledge to that agent
5. Open `Browser Call`
6. Select the agent
7. Start the test call
8. Check transcript/debug state

## 7. Browser voice expectation

Without `GEMINI_API_KEY` and ElevenLabs secrets:

- Browser Call UI should open
- browser session lifecycle can still be exercised
- actual voice should remain blocked

With those secrets present:

- browser voice prerequisites should become ready
- audible browser audio still requires manual validation on your machine

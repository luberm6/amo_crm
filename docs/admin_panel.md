# Admin Panel

## 1. Overview

The admin panel is a separate internal web interface for operating the FastAPI
backend.

It is currently used for:

- managing agent profiles
- editing runtime prompt and conversation rules
- managing company data and knowledge documents
- binding knowledge documents to agents
- running browser-based test calls against the real Direct runtime
- storing and validating provider settings

This panel is an internal operator tool.

Current honest status:

- `PRODUCTION_READY`: no
- `INTEGRATION_READY`: yes
- `NEEDS_REAL_WORLD_VALIDATION`: yes
- `MOCK_ONLY`: no

Why it is not marked `PRODUCTION_READY`:

- browser audio has not been fully confirmed end-to-end by a live manual QA run
- provider settings are implemented as a safe settings layer, not as a full live routing console
- Mango line sync and agent binding are implemented, but inbound webhook and outbound originate are not fully verified live

## 2. Current Capabilities

### Agents

Implemented:

- list agents
- create agent
- edit agent
- soft-disable agent
- inspect assembled prompt preview returned by the backend

Current agent fields:

- `name`
- `is_active`
- `system_prompt`
- `tone_rules`
- `business_rules`
- `sales_objectives`
- `greeting_text`
- `transfer_rules`
- `prohibited_promises`
- `voice_strategy`
- `config`
- `version`

What already affects runtime:

- `system_prompt`
- `tone_rules`
- `business_rules`
- `sales_objectives`
- `greeting_text`
- `transfer_rules`
- `prohibited_promises`
- `voice_strategy`
- `config`
- `voice_provider`
- `telephony_provider`
- `telephony_line_id`
- `telephony_extension`

These fields are not stored just for later editing. They are assembled by the
backend into the runtime configuration used by Direct sessions and Browser Call
when an `agent_profile_id` is provided.

Status:

- `INTEGRATION_READY`
- `NEEDS_REAL_WORLD_VALIDATION` for live conversational quality

### Mango Telephony Binding

Implemented:

- Mango inventory sync into `telephony_lines`
- agent-level telephony binding in Agent Editor
- stable provider-side key exposure via `remote_line_id`
- normalized Mango phone numbers in `+7...` form
- live-confirmed AI line:
  - `remote_line_id=405622036`
  - `phone_number=+79300350609`
  - `schema_name="ДЛЯ ИИ менеджера"`

What the admin panel currently does:

- loads Mango lines for the agent form
- shows line labels using `schema_name` first, then fallback label/number
- saves binding through one agent settings PATCH
- keeps line binding non-blocking even when extensions are unavailable

What is still not confirmed:

- inbound webhook flow
- outbound originate flow
- full PSTN runtime end-to-end

Status:

- `INTEGRATION_READY`
- `NEEDS_REAL_WORLD_VALIDATION`

### Prompt / Rules

Prompt and rule data are stored in `AgentProfile`, not assembled in the UI.

The admin panel edits data only.
The backend assembles the final runtime prompt centrally.

Runtime assembly currently includes:

- `system_prompt`
- `tone_rules`
- `business_rules`
- `sales_objectives`
- `transfer_rules`
- `prohibited_promises`
- controlled knowledge context, when available

This is important:

- prompt logic is centralized on the backend
- the UI does not duplicate prompt assembly rules
- assembled prompt preview shown in the admin panel is backend-generated

Status:

- `INTEGRATION_READY`

### Knowledge Base

Implemented:

- knowledge document CRUD
- category filtering
- active/inactive state
- company profile editor
- explicit binding of knowledge documents to agents

Current knowledge model is intentionally split into:

- `AgentProfile`
- `CompanyProfile`
- `KnowledgeDocument`
- `AgentKnowledgeBinding`

Supported document categories:

- `services`
- `pricing`
- `conditions`
- `faq`
- `scripts`
- `objections`
- `company_policy`

What already works:

- create and update documents
- edit company data separately from agent persona
- bind specific documents to an agent
- keep KB optional, so runtime does not break when KB is empty

Current limitation:

- this is not full retrieval yet
- the system does not blindly inject the entire KB into every prompt
- the backend prepares controlled structured context for later retrieval-oriented evolution

Status:

- `INTEGRATION_READY`
- `NEEDS_REAL_WORLD_VALIDATION` for real conversational effect in live sessions

### Browser Call

Implemented:

- Browser Call is part of the admin panel, not a temporary HTML page
- operator can select an agent
- operator can start and stop a browser session
- transcript and debug information are shown in the UI
- selected agent profile is used by runtime
- browser sessions reuse the existing Direct runtime, not a toy parallel runtime

Current Browser Call path:

`Browser UI -> backend browser endpoints -> BrowserDirectEngine -> DirectSessionManager -> Gemini / TTS -> browser`

What is already wired into runtime:

- selected `AgentProfile`
- backend prompt assembly
- selected `voice_strategy`
- controlled knowledge context from company profile and bound knowledge documents

What is already covered by automated tests:

- create browser session
- stop browser session
- transcript persistence
- cleanup on disconnect
- admin UI screen render

What is still not confirmed live:

- browser microphone capture on a real operator machine
- browser speaker playback on a real operator machine
- audible greeting
- audible AI response
- full manual browser audio QA with working provider credentials

Status:

- `INTEGRATION_READY`
- `NEEDS_REAL_WORLD_VALIDATION`

This distinction matters:
the backend/browser integration is real, but audible browser QA is still a
manual validation step.

### Providers

Implemented:

- DB-backed provider settings storage
- masked secret handling in API responses and UI
- explicit save flow
- explicit validation flow
- provider status surface in the admin panel

Currently supported providers:

- `Mango`
- `Gemini`
- `ElevenLabs`
- `Vapi`

What the provider settings layer does:

- stores credentials/settings
- encrypts stored secrets
- returns only masked secret representations
- allows explicit validation via `Check connection`
- tracks status:
  - `configured`
  - `invalid`
  - `not_tested`
  - `active`
  - `inactive`

What it does not do:

- it does not sync Mango numbers
- it does not assign numbers to agents
- it does not activate AI number routing
- it does not take over shared Mango accounts
- it does not replace amoCRM routing

Special note for Mango:

- Mango validation is intentionally safe-mode validation
- it does not place a call
- it does not trigger number sync
- it does not activate routing

Status:

- `INTEGRATION_READY`
- `NEEDS_REAL_WORLD_VALIDATION` for real credentials/operator QA

## 3. Limitations

These limitations are current and intentional or still unresolved:

- browser audio is not fully confirmed by a real manual live run
- Mango numbers sync is not implemented
- Mango number binding to agents is not implemented
- provider settings are not the same thing as provider routing
- saving provider credentials does not activate a live route automatically
- the admin panel is an internal tool, not a full production operations console
- admin auth is intentionally minimal and is not a full multi-user admin system
- the `Prompts` navigation item exists, but prompt editing is currently centered around `AgentProfile`, not a separate standalone prompt-management domain

## 4. Architecture

### Frontend

Stack:

- `Vite`
- `React`
- `TypeScript`

Frontend location:

- `admin-panel/`

Main areas:

- `src/auth`
- `src/layout`
- `src/pages`
- `src/components`
- `src/lib`
- `src/test`

### Backend

Backend remains:

- `FastAPI`

Admin API currently covers:

- admin auth
- agents CRUD
- knowledge base CRUD
- agent knowledge bindings
- browser call lifecycle
- provider settings

### Runtime Relationship

The admin panel is not just a storage UI.
It is already connected to runtime-relevant entities.

`AgentProfile` is used by runtime to provide:

- assembled prompt
- greeting
- requested voice strategy
- config metadata

`KnowledgeDocument` and `CompanyProfile` are used to build controlled runtime
knowledge context.

`Browser Call` uses the real Direct runtime path through:

- `BrowserDirectEngine`
- `DirectGeminiEngine`
- `DirectSessionManager`

So the admin panel already participates in the real runtime configuration flow.

## 5. How to Run

### Backend

Minimum admin auth configuration:

```bash
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=change-me
ADMIN_AUTH_SECRET=change-me-too
PROVIDER_SETTINGS_SECRET=change-me-three
```

If you want Browser Call with `tts_primary`:

```bash
GEMINI_API_KEY=...
DIRECT_VOICE_STRATEGY=tts_primary
GEMINI_AUDIO_INPUT_ENABLED=true
GEMINI_AUDIO_OUTPUT_ENABLED=false
DIRECT_INITIAL_GREETING_ENABLED=true
ELEVENLABS_ENABLED=true
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...
```

Start backend:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend

Install dependencies:

```bash
cd admin-panel
npm install
```

Start dev server:

```bash
npm run dev
```

Open:

`http://localhost:5173`

## 6. How to Use

### Operator Flow: Create an Agent

1. Open `Agents`
2. Create a new agent
3. Fill in:
   - name
   - system prompt
   - tone rules
   - business rules
   - greeting
   - voice strategy
4. Save
5. Check backend-generated prompt preview

### Operator Flow: Add Company Knowledge

1. Open `Knowledge Base`
2. Save or update company profile
3. Create one or more knowledge documents
4. Choose categories and active state
5. Open an agent
6. Bind the relevant knowledge documents to that agent

### Operator Flow: Run Browser Call

1. Open `Browser Call`
2. Select an agent
3. Click `Start Test Call`
4. Allow microphone access
5. Watch:
   - session id
   - voice strategy
   - active voice path
   - transcript
   - error/debug state
6. Speak to the agent
7. Click `Stop Test Call`

Important:
the UI flow is implemented, but audible browser audio still needs manual QA on a
real machine with working credentials.

### Operator Flow: Save Provider Settings

1. Open `Providers`
2. Save provider config and secrets
3. Confirm secrets are masked after save
4. Run `Check connection`
5. Review status and validation message

Important:
for Mango this still does not activate routing or number usage.

## 7. Next Steps

Short realistic next steps:

- complete manual browser audio validation
- add Mango number inventory sync
- add explicit number binding to agent
- keep provider settings separated from routing activation

## Honest Status Summary

Use the admin panel today as:

- an internal configuration panel
- an internal QA surface
- a runtime-aware editor for agents and knowledge

Do not describe it today as:

- a fully production-validated operator console
- a live number-routing console
- a fully verified browser voice console

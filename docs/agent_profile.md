# Agent Profile

Runtime-editable conversational configuration for AI agents.

Status:
- `PRODUCTION_READY`: no
- `INTEGRATION_READY`: yes
- `NEEDS_REAL_WORLD_VALIDATION`: yes
- `MOCK_ONLY`: no

## Purpose

`AgentProfile` stores conversational behavior in the database instead of keeping
the effective agent prompt hardcoded only in runtime settings.

The admin panel edits data.
The backend assembles runtime prompt centrally.

## Fields

- `name` — human-readable agent name
- `is_active` — enables or disables the profile
- `system_prompt` — core instruction block
- `tone_rules` — style and speaking constraints
- `business_rules` — business-specific logic and restrictions
- `sales_objectives` — desired sales outcomes
- `greeting_text` — first-turn greeting text
- `transfer_rules` — rules for when and how transfer should happen
- `prohibited_promises` — things the agent must never promise
- `voice_strategy` — requested voice strategy for this agent
- `config` — JSON config/metadata for future runtime extensions
- `version` — incremented on every update and soft-disable
- `created_at` / `updated_at`

## CRUD API

Protected by admin bearer auth.

- `GET /v1/agents`
- `GET /v1/agents/{id}`
- `POST /v1/agents`
- `PATCH /v1/agents/{id}`
- `DELETE /v1/agents/{id}`

Current delete behavior:
- soft-disable
- sets `is_active=false`
- keeps the row for auditability
- increments `version`

## Runtime Assembly

Central assembly lives in:

- [agent_profile_service.py](/Users/iluxa/Amo_crm/app/services/agent_profile_service.py)

Key functions:
- `assemble_agent_system_prompt(agent)`
- `build_agent_runtime_configuration(agent)`

Current assembly output joins non-empty sections into one effective runtime prompt.

That same assembled prompt is used for:
- API preview in `GET /v1/agents/{id}`
- Direct runtime startup when a call/browser session is created with `agent_profile_id`

## Runtime Usage Today

Current real integration:
- `Call` has optional `agent_profile_id`
- browser call session can be started with `agent_profile_id`
- `DirectGeminiEngine` resolves:
  - assembled `system_prompt`
  - `greeting_text`
  - requested `voice_strategy`

So the profile is not just stored for later. It already feeds the Direct session path.

## Important Limits

1. Per-agent prompt and greeting are wired into runtime now.
2. Per-agent `voice_strategy` is passed into Direct session startup, but still depends on global backend capabilities:
   - Gemini audio flags
   - ElevenLabs availability
   - global environment setup
3. This is not proof of live production voice behavior. Real call validation is still separate.

## Admin Panel

The admin panel exposes:
- agent list
- create
- edit
- disable
- assembled prompt preview

The UI does not assemble the final prompt itself.
Preview is returned by the backend.

## Manual QA Checklist

1. Open `Agents`
2. Create a new profile with:
   - system prompt
   - greeting
   - tone rules
   - business rules
3. Save
4. Confirm:
   - profile appears in list
   - `version=1`
   - assembled prompt preview is visible
5. Edit:
   - sales objectives
   - prohibited promises
   - voice strategy
6. Save again
7. Confirm:
   - `version` increments
   - `updated_at` changes
   - preview updates
8. Open `Browser Call`
9. Select the agent profile
10. Start a browser session and confirm the runtime starts with the selected agent configuration

## Next Step

Natural continuation:
- connect `Knowledge Base` and retrieval config to `AgentProfile.config`
- extend runtime assembly with KB-derived context in one central place

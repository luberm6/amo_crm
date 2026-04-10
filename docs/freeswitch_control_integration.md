# FreeSWITCH Control Integration

## Scope
This document covers the first real control/media integration step:
- backend connects to FreeSWITCH via ESL;
- backend sends control commands;
- backend receives and normalizes core channel events;
- backend tracks session lifecycle and correlation.

It does not claim full production voice readiness.

## Connection Model
- Transport: FreeSWITCH inbound ESL (TCP).
- Auth: ESL password (`freeswitch_esl_password`).
- Client: `FreeSwitchEslClient`.
- Lifecycle manager: `FreeSwitchMediaGateway` (`mode=esl_rtp`).

Implemented ESL lifecycle:
- connect + authenticate;
- subscribe to configured events;
- event read loop;
- reconnect with exponential backoff when connection/event loop fails.

## Config
Required settings:
- `MEDIA_GATEWAY_ENABLED=true`
- `MEDIA_GATEWAY_PROVIDER=freeswitch`
- `MEDIA_GATEWAY_MODE=esl_rtp`
- `FREESWITCH_ESL_HOST`
- `FREESWITCH_ESL_PORT`
- `FREESWITCH_ESL_PASSWORD`

Reconnect settings:
- `FREESWITCH_ESL_CONNECT_TIMEOUT_SECONDS`
- `FREESWITCH_ESL_RECONNECT_ENABLED`
- `FREESWITCH_ESL_RECONNECT_INITIAL_DELAY_SECONDS`
- `FREESWITCH_ESL_RECONNECT_MAX_DELAY_SECONDS`
- `FREESWITCH_ESL_RECONNECT_MAX_ATTEMPTS`

## Event Normalization
Normalized events (from ESL `text/event-plain`):
- `CHANNEL_CREATE` -> `channel_create`
- `CHANNEL_ANSWER` -> `channel_answer`
- `CHANNEL_HANGUP` / `CHANNEL_HANGUP_COMPLETE` -> `channel_hangup`
- `PLAYBACK_START` -> `playback_start`
- `PLAYBACK_STOP` -> `playback_stop`
- `CHANNEL_BRIDGE` -> `channel_bridge`
- `CUSTOM` with `Event-Subclass=ai::barge_in` -> `barge_in`
- `HEARTBEAT` -> `heartbeat`

Behavior:
- `channel_hangup` emits media event `HANGUP` into session event stream.
- `barge_in` emits media event `BARGE_IN`.
- other normalized events are emitted as `HEARTBEAT` with payload, and update session lifecycle state.

## Correlation Model
Per session stored in gateway:
- `call_id` (internal backend call id)
- `session_id` (gateway session id)
- `mango_leg_id` (provider leg id used for attach)
- `freeswitch_uuid` (updated from ESL `Unique-ID` / `Channel-Call-UUID`)

Available inspection methods:
- `get_session_correlation(session_id)`
- `get_session_lifecycle(session_id)`

Lifecycle flags tracked:
- `created`
- `answered`
- `bridged`
- `playback_active`
- `hungup`
- `last_event`

## What Works Now
- ESL connect/auth/subscribe.
- ESL reconnect/backoff handling.
- Command execution via `execute_command(...)`.
- Media session attach/detach with correlation and lifecycle tracking.
- Hangup/playback/answer/bridge event normalization from ESL frames.

## Known Limits
- Live FreeSWITCH event schemas may vary by deployment profile/modules.
- Command templates (`uuid_media_reneg`, `uuid_kill`) may require tenant-specific tuning.
- No claim of end-to-end real voice quality until live contour validation is done.

## Manual Smoke Checklist
1. Run FreeSWITCH with `mod_event_socket` and confirm ESL auth credentials.
2. Start backend with `MEDIA_GATEWAY_MODE=esl_rtp`.
3. Trigger session attach (direct mode call attach path).
4. Verify backend logs:
   - `freeswitch_gateway.esl_connected`
   - `freeswitch_gateway.session_attached`
5. Execute a test command from backend path using gateway command method.
6. Generate channel events (answer, playback, hangup) and verify logs:
   - `freeswitch_gateway.event event=channel_answer`
   - `... event=playback_start`
   - `... event=channel_hangup`
7. Verify `get_session_lifecycle(...)` reflects transitions.
8. Verify session event stream emits `HANGUP` and closes cleanly.

## Readiness
- `PRODUCTION_READY`: no.
- `INTEGRATION_READY`: ESL control client, reconnect logic, normalized events, lifecycle/correlation tracking.
- `NEEDS_REAL_WORLD_VALIDATION`: live Mango trunk + FreeSWITCH behavior, event semantics, timing and network conditions.
- `MOCK_ONLY`: `MEDIA_GATEWAY_MODE=mock`.

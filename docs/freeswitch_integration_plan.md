# FreeSWITCH Integration Plan

## Scope
This plan covers the media-plane rollout for:
`Mango <-> FreeSWITCH <-> Python Backend <-> Gemini/ElevenLabs`.

## Phase Plan

### Phase 0 (implemented now)
- Add gateway contracts:
  - session attach/detach
  - inbound events
  - outbound audio send
  - barge-in signal
  - hangup propagation
- Add FreeSWITCH gateway scaffold and explicit non-production behavior.
- Add mock mode and architecture tests.

### Phase 1 (implemented baseline)
- ESL control channel:
  - connect/authenticate
  - bgapi command execution
  - subscribe to event socket events
- Session mapping:
  - provider leg id <-> backend session id
- Basic RTP ingest/inject:
  - UDP RTP receive, payload extraction, PCM forwarding
  - RTP packet build and UDP send to learned remote endpoint

### Phase 2
- Outbound media injection:
  - low-jitter playout path from backend to FreeSWITCH.
- Barge-in:
  - cutoff TTS stream and signal model interruption.
- Hangup synchronization:
  - ensure single terminal transition, no duplicate cleanup races.

### Phase 3
- Real-time tuning:
  - latency budget and jitter control
  - backpressure handling on AI/TTS slowdowns
  - RTP quality metrics and alerting
- Production hardening:
  - NAT traversal, firewall policies, TLS/SRTP where required
  - failure drills and restart reconciliation

## Contracts Introduced
- `AbstractMediaGateway`
  - `attach_session()`
  - `detach_session()`
  - `events()`
  - `send_audio()`
  - `send_barge_in()`
  - `propagate_hangup()`
- `FreeSwitchAudioBridge`
  - bridge between Direct session loop and media gateway events/audio API.

## Deployment Checklist (manual)
1. Install FreeSWITCH (same region as backend).
2. Enable SIP profile for Mango trunk (`external` profile baseline).
3. Configure ACL/firewall for SIP/RTP ranges.
4. Configure ESL listener (`event_socket.conf.xml`) and password.
5. Point Mango SIP trunk to FreeSWITCH public SIP endpoint.
6. Set backend env:
   - `MEDIA_GATEWAY_ENABLED=true`
   - `MEDIA_GATEWAY_PROVIDER=freeswitch`
   - `MEDIA_GATEWAY_MODE=esl_rtp` (baseline ESL+RTP path)
   - FreeSWITCH host/port/password/profile/domain/RTP settings.
7. Run control-plane smoke:
   - originate -> answer -> transfer -> hangup.
8. Run media smoke on live contour:
   - customer speech reaches backend,
   - AI response audible to customer,
   - barge-in interrupts playback.

## Smoke Scenarios
1. Outbound call answered, media session attaches once.
2. Inbound audio frames continue for 30+ seconds.
3. AI playback is heard by customer.
4. User interruption stops current playback and model continues.
5. Customer hangup tears down media session and call state cleanly.
6. Warm transfer bridge confirmed, AI media session detached, no orphan RTP stream.

## Known Blockers
- ESL/RTP baseline exists, but command templates and payload assumptions are tenant-specific and need live tuning.
- No production latency/SRTP/NAT validation has been executed.
- No live end-to-end validation for `customer -> Gemini -> PSTN playback via Mango trunk`.
- Prometheus scrape endpoint exists, but production dashboards/alerts/runbooks are not configured yet.

## Readiness
- `PRODUCTION_READY`: no.
- `INTEGRATION_READY`: scaffolding/contracts + baseline `esl_rtp` runtime path.
- `NEEDS_REAL_WORLD_VALIDATION`: all real RTP/ESL behavior.
- `MOCK_ONLY`: `MEDIA_GATEWAY_MODE=mock`.

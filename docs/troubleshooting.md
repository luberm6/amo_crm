# Troubleshooting (Mango + FreeSWITCH + Direct Voice)

## 1) Call does not start
Symptoms:
- `POST /v1/calls` returns 4xx/5xx
- no Mango leg id in logs

Checks:
1. `MANGO_API_KEY`, `MANGO_API_SALT`, `MANGO_FROM_EXT` are set.
2. `telephony_provider` resolves to Mango (or auto with Mango configured).
3. API key auth (`X-API-Key`) is correct.
4. Quiet hours / abuse policies not blocking request.

## 2) Call starts but FreeSWITCH sees no channel
Symptoms:
- Mango call created, but no `CHANNEL_CREATE` in backend logs.

Checks:
1. Mango trunk routing to FreeSWITCH is correct (DID/extension/context).
2. FreeSWITCH SIP profile is listening on expected interface/port.
3. Firewall allows SIP between Mango and FreeSWITCH.
4. Verify Mango side route for this exact number/line.

## 3) Backend gets no audio
Symptoms:
- call is connected, but inbound frame counters do not grow.

Checks:
1. `MEDIA_GATEWAY_MODE=esl_rtp`.
2. RTP ports (`FREESWITCH_RTP_PORT_START/END`) open bidirectionally.
3. Attach command template is valid for your FS deployment.
4. Codec mismatch:
- check `FREESWITCH_RTP_INBOUND_CODEC` (`pcm16` vs `pcmu`).

## 4) Gemini does not answer
Symptoms:
- inbound frames exist, no assistant output.

Checks:
1. `GEMINI_API_KEY` valid and model reachable.
2. `GEMINI_AUDIO_INPUT_ENABLED=true` for audio-in path.
3. For native audio reply: `GEMINI_AUDIO_OUTPUT_ENABLED=true`.
4. Inspect model latency metric and websocket errors in logs.

## 5) Caller does not hear AI
Symptoms:
- assistant text exists, no audible playback.

Checks:
1. Native audio mode:
- confirm Gemini emits audio chunks.
2. Fallback mode:
- `ELEVENLABS_ENABLED=true`, key + voice id valid.
3. Outbound RTP path:
- remote RTP endpoint learned;
- no continuous underrun increments.
4. Codec alignment:
- `FREESWITCH_RTP_OUTBOUND_CODEC` matches expected call leg media.

## 6) Transfer fails
Symptoms:
- manager not bridged / transfer stuck in non-terminal state.

Checks:
1. Mango webhook delivery to `/v1/webhooks/mango`.
2. Redis available (state/correlation durability).
3. Manager availability and cooldown state.
4. Bridge/whisper event semantics on your tenant payload.

## 7) Cleanup does not complete
Symptoms:
- lingering sessions/tasks after stop/hangup.

Checks:
1. Verify stop endpoint called and accepted.
2. Check for `session_manager.terminated` log.
3. Confirm bridge close succeeded.
4. Check FreeSWITCH hangup events reached backend.

## 8) Fast triage commands
1. Health/readiness:
```bash
curl http://<backend>/health
curl http://<backend>/ready
```
2. Metrics:
```bash
curl http://<backend>/metrics
```
3. Active calls:
```bash
curl http://<backend>/v1/calls/active
```

## 9) Known infra blockers (still real)
1. No live signed-off E2E validation on production-like Mango tenant contour.
2. No finalized quality/SRTP/NAT validation report.
3. No completed alert threshold tuning for voice quality incidents.

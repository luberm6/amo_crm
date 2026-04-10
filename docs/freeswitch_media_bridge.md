# FreeSWITCH Media Bridge (First Working Path)

## Selected Media Path
Chosen path: **RTP UDP media via FreeSWITCH + ESL control** (`media_gateway_mode=esl_rtp`).

Why this path:
- already integrated with current control plane (ESL event loop exists);
- minimal moving parts for first real audio path;
- no extra FreeSWITCH modules required for initial inbound capture.

## What Is Implemented Now
- Real inbound audio capture:
  - backend opens RTP endpoint per session;
  - FreeSWITCH sends RTP to backend endpoint;
  - backend emits `MediaEvent(AUDIO_IN)` with decoded audio.
- Codec conversion:
  - inbound: RTP payload can be decoded as `pcmu` to PCM16;
  - outbound: PCM16 can be encoded to `pcmu` before RTP send.
- Outbound path:
  - backend sends RTP back to learned remote RTP endpoint;
  - marked as **partial** until live PSTN audibility is validated.
- Media session state:
  - frame/byte counters in/out;
  - overruns/underruns counters;
  - disconnect reason;
  - RTP timeout watchdog (`rtp_timeout` -> hangup event).

## Runtime Controls
Config:
- `FREESWITCH_RTP_INBOUND_CODEC` (`pcm16` | `pcmu`)
- `FREESWITCH_RTP_OUTBOUND_CODEC` (`pcm16` | `pcmu`)
- `FREESWITCH_RTP_SAMPLE_RATE_HZ`
- `FREESWITCH_RTP_FRAME_BYTES`
- `FREESWITCH_RTP_INBOUND_TIMEOUT_SECONDS`
- `FREESWITCH_EVENT_QUEUE_MAX`

Behavior:
- bounded event queue with overrun accounting;
- no silent frame drop (overruns logged and counted);
- outbound before remote RTP discovery increments underrun and raises explicit error.

## What Works (Code + Tests)
- inbound RTP capture to backend event stream;
- basic outbound RTP packet send path;
- PCMU<->PCM16 conversion helpers;
- timeout/hangup propagation from media watchdog;
- stats counters growing in tests.

## What Is Incomplete
- full-duplex quality validation on live call contour;
- verified customer-audible playback via Mango trunk in production-like network;
- production tuning for jitter buffer, NAT/SRTP, codec negotiation edge cases.

## Manual Smoke Checklist
1. Start FreeSWITCH with ESL enabled (`mod_event_socket`).
2. Start backend with:
   - `MEDIA_GATEWAY_ENABLED=true`
   - `MEDIA_GATEWAY_PROVIDER=freeswitch`
   - `MEDIA_GATEWAY_MODE=esl_rtp`
3. Initiate call path that attaches FreeSWITCH media session.
4. Confirm logs:
   - `freeswitch_gateway.session_attached`
   - `freeswitch_gateway.event fs_event=channel_answer`
5. Speak on call leg and verify backend receives `AUDIO_IN` events (or counters increase).
6. Check media stats:
   - `frames_in` and `bytes_in` increase;
   - no growing overrun/underrun under normal load.
7. Hang up call and verify `HANGUP` propagation and clean session close.

## Readiness
- `INTEGRATION_READY`: first real inbound media path + partial outbound path.
- `NEEDS_REAL_WORLD_VALIDATION`: live PSTN audibility, latency/jitter/NAT/SRTP.
- `MOCK_ONLY`: `media_gateway_mode=mock`.
- `PRODUCTION_READY`: not yet.

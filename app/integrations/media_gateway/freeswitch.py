from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import errno
import random
import socket
import struct
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

from app.core.logging import get_logger
from app.core.telemetry import (
    inc_fs_error,
    inc_fs_esl_event,
    inc_fs_rtp_in,
    inc_fs_rtp_out,
    inc_fs_session_attach,
    inc_fs_session_detach,
    set_fs_active_sessions,
)
from app.integrations.telephony.base import TelephonyLegState
from app.integrations.telephony.mango_freeswitch_correlation import (
    get_mango_freeswitch_correlation_store,
)
from app.integrations.media_gateway.esl_client import FreeSwitchEslClient
from app.integrations.media_gateway.base import (
    AbstractMediaGateway,
    MediaEvent,
    MediaEventType,
    MediaGatewayNotReadyError,
    MediaSessionHandle,
)

log = get_logger(__name__)


@dataclass
class FreeSwitchGatewayConfig:
    mode: str = "disabled"  # disabled | mock | scaffold | esl_rtp
    esl_host: str = "127.0.0.1"
    esl_port: int = 8021
    esl_password: str = "ClueCon"
    sip_profile: str = "external"
    sip_domain: str = "localhost"
    rtp_ip: str = "127.0.0.1"
    rtp_port_start: int = 16384
    rtp_port_end: int = 32768
    session_timeout_seconds: int = 120
    rtp_payload_type: int = 96
    attach_command_template: str = (
        "uuid_media_reneg {uuid} ={rtp_ip}:{rtp_port}"
    )
    hangup_command_template: str = "uuid_kill {uuid}"
    esl_events: str = "CHANNEL_HANGUP_COMPLETE CUSTOM HEARTBEAT"
    esl_connect_timeout_seconds: float = 5.0
    esl_reconnect_enabled: bool = True
    esl_reconnect_initial_delay_seconds: float = 0.5
    esl_reconnect_max_delay_seconds: float = 5.0
    esl_reconnect_max_attempts: int = 0
    rtp_inbound_codec: str = "pcm16"   # pcm16 | pcmu
    rtp_outbound_codec: str = "pcm16"  # pcm16 | pcmu
    rtp_sample_rate_hz: int = 16000
    rtp_frame_bytes: int = 640
    rtp_inbound_timeout_seconds: int = 15
    rtp_outbound_buffer_max_frames: int = 50
    event_queue_max: int = 512


@dataclass
class _RtpRuntime:
    transport: asyncio.DatagramTransport
    local_ip: str
    local_port: int
    remote_addr: Optional[tuple[str, int]] = None
    pending_outbound: list[bytes] = field(default_factory=list)
    seq: int = 0
    ts: int = 0
    ssrc: int = 0


@dataclass
class _SessionCorrelation:
    session_id: str
    call_id: str
    mango_leg_id: str
    freeswitch_uuid: Optional[str] = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class _SessionLifecycle:
    created: bool = False
    answered: bool = False
    bridged: bool = False
    playback_active: bool = False
    hungup: bool = False
    last_event: Optional[str] = None
    updated_at: Optional[datetime] = None


@dataclass
class _SessionMediaStats:
    frames_in: int = 0
    bytes_in: int = 0
    frames_out: int = 0
    bytes_out: int = 0
    overruns: int = 0
    underruns: int = 0
    disconnect_reason: Optional[str] = None
    last_rtp_at: Optional[datetime] = None


class FreeSwitchMediaGateway(AbstractMediaGateway):
    """
    FreeSWITCH media gateway integration layer.

    Modes:
    - disabled: attach fails explicitly.
    - scaffold: explicit non-production path.
    - mock: deterministic in-memory event bus for architecture tests.
    - esl_rtp: real ESL command/event loop + RTP UDP ingest/inject.
    """

    def __init__(self, config: FreeSwitchGatewayConfig) -> None:
        self._cfg = config
        self._queues: dict[str, asyncio.Queue[MediaEvent]] = {}
        self._handles: dict[str, MediaSessionHandle] = {}
        self._closed: set[str] = set()
        self._lock = asyncio.Lock()
        self._audio_out_bytes: dict[str, int] = {}
        self._rtp: dict[str, _RtpRuntime] = {}
        self._leg_to_session: dict[str, str] = {}
        self._uuid_to_session: dict[str, str] = {}
        self._correlation: dict[str, _SessionCorrelation] = {}
        self._lifecycle: dict[str, _SessionLifecycle] = {}
        self._media_stats: dict[str, _SessionMediaStats] = {}
        self._rtp_watchdog_tasks: dict[str, asyncio.Task] = {}
        self._esl: Optional[FreeSwitchEslClient] = None
        self._esl_reader_task: Optional[asyncio.Task] = None
        self._esl_connect_lock = asyncio.Lock()
        self._corr = get_mango_freeswitch_correlation_store()
        self._last_esl_error: Optional[dict[str, Any]] = None

    async def attach_session(
        self,
        *,
        call_id: str,
        provider_leg_id: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> MediaSessionHandle:
        if self._cfg.mode == "disabled":
            raise MediaGatewayNotReadyError(
                "Media gateway is disabled. Set MEDIA_GATEWAY_ENABLED=true and configure gateway mode."
            )
        if self._cfg.mode == "scaffold":
            raise MediaGatewayNotReadyError(
                "FreeSWITCH scaffold mode is configured but RTP/ESL plumbing is not implemented yet. "
                "This runtime path is explicitly non-production."
            )

        if self._cfg.mode == "esl_rtp":
            await self._ensure_esl_connected()

        session_id = f"fs-{uuid.uuid4().hex[:16]}"
        handle = MediaSessionHandle(
            session_id=session_id,
            call_id=call_id,
            provider_leg_id=provider_leg_id,
            metadata=metadata or {},
        )
        async with self._lock:
            self._queues[session_id] = asyncio.Queue(maxsize=max(1, int(self._cfg.event_queue_max)))
            self._handles[session_id] = handle
            self._audio_out_bytes[session_id] = 0
            self._leg_to_session[provider_leg_id] = session_id
            self._uuid_to_session[provider_leg_id] = session_id
            self._correlation[session_id] = _SessionCorrelation(
                session_id=session_id,
                call_id=call_id,
                mango_leg_id=provider_leg_id,
                freeswitch_uuid=provider_leg_id,
                updated_at=datetime.now(timezone.utc),
            )
            self._lifecycle[session_id] = _SessionLifecycle(
                last_event="attached",
                updated_at=datetime.now(timezone.utc),
            )
            self._media_stats[session_id] = _SessionMediaStats()
            set_fs_active_sessions(self._cfg.mode, len(self._handles))
        inc_fs_session_attach(self._cfg.mode)
        await self._corr.upsert_mapping(
            mango_leg_id=provider_leg_id,
            call_id=call_id,
            freeswitch_uuid=provider_leg_id,
            freeswitch_session_id=session_id,
        )

        if self._cfg.mode == "esl_rtp":
            rtp = await self._open_rtp_runtime(session_id)
            self._rtp[session_id] = rtp
            if self._cfg.rtp_inbound_timeout_seconds > 0:
                self._rtp_watchdog_tasks[session_id] = asyncio.create_task(
                    self._rtp_watchdog_loop(session_id),
                    name=f"rtp_watchdog_{session_id}",
                )
            handle.metadata = {
                **(handle.metadata or {}),
                "gateway_mode": "esl_rtp",
                "rtp_local_ip": rtp.local_ip,
                "rtp_local_port": rtp.local_port,
                "inbound_codec": self._cfg.rtp_inbound_codec,
                "outbound_codec": self._cfg.rtp_outbound_codec,
                "sample_rate_hz": self._cfg.rtp_sample_rate_hz,
                "frame_bytes": self._cfg.rtp_frame_bytes,
            }
            await self._run_attach_command(provider_leg_id, rtp.local_ip, rtp.local_port)

        log.info("freeswitch_gateway.session_attached", session_id=session_id, call_id=call_id)
        return handle

    async def detach_session(self, session_id: str) -> None:
        handle = self._handles.get(session_id)
        wd = self._rtp_watchdog_tasks.pop(session_id, None)
        if wd is not None:
            wd.cancel()
            try:
                await wd
            except asyncio.CancelledError:
                pass
        if self._cfg.mode == "esl_rtp" and handle is not None:
            await self._run_hangup_command(handle.provider_leg_id)

        rtp = self._rtp.pop(session_id, None)
        if rtp is not None:
            rtp.transport.close()

        async with self._lock:
            self._closed.add(session_id)
            self._queues.pop(session_id, None)
            self._handles.pop(session_id, None)
            self._audio_out_bytes.pop(session_id, None)
            if handle is not None:
                self._leg_to_session.pop(handle.provider_leg_id, None)
                self._uuid_to_session.pop(handle.provider_leg_id, None)
            corr = self._correlation.pop(session_id, None)
            if corr and corr.freeswitch_uuid:
                self._uuid_to_session.pop(corr.freeswitch_uuid, None)
            self._lifecycle.pop(session_id, None)
            stats = self._media_stats.pop(session_id, None)
            if stats is not None and stats.disconnect_reason is None:
                stats.disconnect_reason = "detached"
            set_fs_active_sessions(self._cfg.mode, len(self._handles))
        inc_fs_session_detach(self._cfg.mode)
        log.info("freeswitch_gateway.session_detached", session_id=session_id)

    async def events(self, session_id: str) -> AsyncIterator[MediaEvent]:
        while True:
            if session_id in self._closed:
                return
            q = self._queues.get(session_id)
            if q is None:
                return
            evt = await q.get()
            yield evt
            if evt.type == MediaEventType.HANGUP:
                return

    async def send_audio(self, session_id: str, pcm: bytes) -> None:
        if self._cfg.mode in ("disabled", "scaffold"):
            raise MediaGatewayNotReadyError(
                "FreeSWITCH send_audio is unavailable outside mock mode."
            )
        if session_id not in self._handles:
            inc_fs_error("send_audio_missing_session")
            log.warning(
                "freeswitch_gateway.send_audio_missing_session",
                session_id=session_id,
                bytes=len(pcm or b""),
            )
            return
        stats = self._media_stats.get(session_id)
        self._audio_out_bytes[session_id] = self._audio_out_bytes.get(session_id, 0) + len(pcm)
        if self._cfg.mode == "esl_rtp":
            runtime = self._rtp.get(session_id)
            if runtime is None:
                raise MediaGatewayNotReadyError("RTP runtime missing for session.")
            if runtime.remote_addr is None:
                max_frames = max(1, int(self._cfg.rtp_outbound_buffer_max_frames))
                if len(runtime.pending_outbound) >= max_frames:
                    runtime.pending_outbound.pop(0)
                    if stats is not None:
                        stats.underruns += 1
                runtime.pending_outbound.append(pcm)
                log.info(
                    "freeswitch_gateway.audio_buffered_waiting_remote",
                    session_id=session_id,
                    buffered_frames=len(runtime.pending_outbound),
                    bytes=len(pcm),
                )
                return
            self._send_rtp_audio(session_id, runtime, pcm)

    def _send_rtp_audio(self, session_id: str, runtime: _RtpRuntime, pcm: bytes) -> None:
        stats = self._media_stats.get(session_id)
        if runtime.remote_addr is None:
            raise MediaGatewayNotReadyError("RTP remote endpoint unknown.")
        encoded = _encode_outbound_audio(pcm, self._cfg.rtp_outbound_codec)
        pkt = _build_rtp_packet(
            pcm=encoded,
            payload_type=self._cfg.rtp_payload_type,
            seq=runtime.seq,
            ts=runtime.ts,
            ssrc=runtime.ssrc,
        )
        runtime.transport.sendto(pkt, runtime.remote_addr)
        runtime.seq = (runtime.seq + 1) % 65536
        sample_div = 2 if self._cfg.rtp_outbound_codec == "pcm16" else 1
        runtime.ts = (runtime.ts + max(1, len(encoded) // sample_div)) % (2**32)
        if stats is not None:
            stats.frames_out += 1
            stats.bytes_out += len(encoded)
        inc_fs_rtp_out(len(encoded), self._cfg.mode)

    def _try_set_remote_addr_from_event(
        self,
        session_id: str,
        raw_event: dict[str, str],
    ) -> None:
        runtime = self._rtp.get(session_id)
        if runtime is None or runtime.remote_addr is not None:
            return
        host = (
            raw_event.get("variable_remote_media_ip")
            or raw_event.get("variable_remote_audio_media_ip")
            or raw_event.get("variable_rtp_audio_remote_addr")
        )
        port_raw = (
            raw_event.get("variable_remote_media_port")
            or raw_event.get("variable_remote_audio_media_port")
            or raw_event.get("variable_rtp_audio_remote_port")
        )
        if not host or not port_raw:
            return
        try:
            port = int(port_raw)
        except (TypeError, ValueError):
            return
        runtime.remote_addr = (host, port)
        log.info(
            "freeswitch_gateway.remote_endpoint_primed",
            session_id=session_id,
            remote_ip=host,
            remote_port=port,
        )
        self._flush_pending_audio(session_id, runtime)

    def _flush_pending_audio(self, session_id: str, runtime: _RtpRuntime) -> None:
        if runtime.remote_addr is None or not runtime.pending_outbound:
            return
        pending = list(runtime.pending_outbound)
        runtime.pending_outbound.clear()
        for chunk in pending:
            self._send_rtp_audio(session_id, runtime, chunk)
        log.info(
            "freeswitch_gateway.audio_buffer_flushed",
            session_id=session_id,
            flushed_frames=len(pending),
        )

    async def send_barge_in(self, session_id: str) -> None:
        if session_id not in self._queues:
            return
        await self._queue_event(session_id, MediaEvent(type=MediaEventType.BARGE_IN))

    async def propagate_hangup(self, session_id: str, *, reason: Optional[str] = None) -> None:
        if session_id not in self._queues:
            return
        await self._queue_event(session_id, MediaEvent(type=MediaEventType.HANGUP, reason=reason))

    async def health(self) -> dict[str, Any]:
        diag = await self._esl_target_diagnostics()
        return {
            "provider": "freeswitch",
            "mode": self._cfg.mode,
            "active_sessions": len(self._handles),
            "esl_connected": bool(self._esl is not None and self._esl.connected),
            "esl_host": self._cfg.esl_host,
            "esl_port": self._cfg.esl_port,
            "esl_target": f"{self._cfg.esl_host}:{self._cfg.esl_port}",
            "esl_dns": diag,
            "last_esl_error": self._last_esl_error,
        }

    async def _esl_target_diagnostics(self) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        try:
            addrinfo = await asyncio.wait_for(
                loop.getaddrinfo(
                    self._cfg.esl_host,
                    self._cfg.esl_port,
                    type=socket.SOCK_STREAM,
                ),
                timeout=float(self._cfg.esl_connect_timeout_seconds),
            )
        except asyncio.TimeoutError:
            return {
                "status": "timeout",
                "host": self._cfg.esl_host,
                "port": self._cfg.esl_port,
            }
        except socket.gaierror as exc:
            return {
                "status": "dns_failure",
                "host": self._cfg.esl_host,
                "port": self._cfg.esl_port,
                "error": str(exc),
            }
        except Exception as exc:
            return {
                "status": "error",
                "host": self._cfg.esl_host,
                "port": self._cfg.esl_port,
                "error": str(exc),
            }

        targets: list[str] = []
        for item in addrinfo:
            sockaddr = item[4]
            if isinstance(sockaddr, tuple) and len(sockaddr) >= 2:
                targets.append(f"{sockaddr[0]}:{sockaddr[1]}")

        return {
            "status": "resolved",
            "host": self._cfg.esl_host,
            "port": self._cfg.esl_port,
            "targets": targets,
        }

    @staticmethod
    def _classify_esl_error(exc: Exception) -> str:
        if isinstance(exc, asyncio.TimeoutError):
            return "timeout"
        if isinstance(exc, socket.gaierror):
            return "dns_failure"
        if isinstance(exc, ConnectionRefusedError):
            return "connection_refused"
        if isinstance(exc, OSError):
            if exc.errno == errno.ECONNREFUSED:
                return "connection_refused"
            if exc.errno in {errno.ETIMEDOUT, errno.EHOSTUNREACH, errno.ENETUNREACH}:
                return "timeout"
        return exc.__class__.__name__.lower()

    def get_session_correlation(self, session_id: str) -> Optional[dict[str, Any]]:
        corr = self._correlation.get(session_id)
        if corr is None:
            return None
        return {
            "session_id": corr.session_id,
            "call_id": corr.call_id,
            "mango_leg_id": corr.mango_leg_id,
            "freeswitch_uuid": corr.freeswitch_uuid,
            "updated_at": corr.updated_at.isoformat(),
        }

    def get_session_lifecycle(self, session_id: str) -> Optional[dict[str, Any]]:
        st = self._lifecycle.get(session_id)
        if st is None:
            return None
        return {
            "created": st.created,
            "answered": st.answered,
            "bridged": st.bridged,
            "playback_active": st.playback_active,
            "hungup": st.hungup,
            "last_event": st.last_event,
            "updated_at": st.updated_at.isoformat() if st.updated_at else None,
        }

    async def execute_command(self, command: str, *, background: bool = True) -> str:
        await self._ensure_esl_connected()
        if self._esl is None:
            raise MediaGatewayNotReadyError("ESL not connected")
        return await self._esl.send_api(command, background=background)

    # Test-only hooks for deterministic architecture tests.
    async def inject_inbound_audio(self, session_id: str, pcm: bytes) -> None:
        if session_id not in self._queues:
            return
        await self._queue_event(session_id, MediaEvent(type=MediaEventType.AUDIO_IN, pcm=pcm))

    def get_audio_out_bytes(self, session_id: str) -> int:
        return self._audio_out_bytes.get(session_id, 0)

    def get_media_stats(self, session_id: str) -> Optional[dict[str, Any]]:
        st = self._media_stats.get(session_id)
        if st is None:
            return None
        return {
            "frames_in": st.frames_in,
            "bytes_in": st.bytes_in,
            "frames_out": st.frames_out,
            "bytes_out": st.bytes_out,
            "overruns": st.overruns,
            "underruns": st.underruns,
            "disconnect_reason": st.disconnect_reason,
            "last_rtp_at": st.last_rtp_at.isoformat() if st.last_rtp_at else None,
        }

    async def _queue_event(self, session_id: str, evt: MediaEvent) -> None:
        q = self._queues.get(session_id)
        if q is None:
            return
        try:
            q.put_nowait(evt)
            return
        except asyncio.QueueFull:
            stats = self._media_stats.get(session_id)
            if stats is not None:
                stats.overruns += 1
            log.warning(
                "freeswitch_gateway.event_queue_overrun",
                session_id=session_id,
                event_type=evt.type.value,
            )
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                q.put_nowait(evt)
            except asyncio.QueueFull:
                if stats is not None:
                    stats.overruns += 1

    async def _rtp_watchdog_loop(self, session_id: str) -> None:
        timeout = float(self._cfg.rtp_inbound_timeout_seconds)
        if timeout <= 0:
            return
        try:
            while session_id in self._handles:
                await asyncio.sleep(1.0)
                st = self._media_stats.get(session_id)
                if st is None or st.last_rtp_at is None:
                    continue
                age = (datetime.now(timezone.utc) - st.last_rtp_at).total_seconds()
                if age <= timeout:
                    continue
                st.disconnect_reason = "rtp_timeout"
                await self._queue_event(
                    session_id,
                    MediaEvent(type=MediaEventType.HANGUP, reason="rtp_timeout"),
                )
                return
        except asyncio.CancelledError:
            return

    async def _ensure_esl_connected(self) -> None:
        if self._esl is not None and self._esl.connected:
            return
        async with self._esl_connect_lock:
            if self._esl is not None and self._esl.connected:
                return
            delay = max(0.05, float(self._cfg.esl_reconnect_initial_delay_seconds))
            max_delay = max(delay, float(self._cfg.esl_reconnect_max_delay_seconds))
            attempts = 0
            while True:
                attempts += 1
                diag = await self._esl_target_diagnostics()
                try:
                    client = self._build_esl_client()
                    await client.connect()
                    await client.subscribe_events(self._cfg.esl_events)
                    self._esl = client
                    self._last_esl_error = None
                    log.info(
                        "freeswitch_gateway.esl_connected",
                        host=self._cfg.esl_host,
                        port=self._cfg.esl_port,
                        attempts=attempts,
                        dns=diag,
                    )
                    if self._esl_reader_task is None or self._esl_reader_task.done():
                        self._esl_reader_task = asyncio.create_task(
                            self._esl_event_loop(),
                            name="freeswitch_esl_event_loop",
                        )
                    return
                except Exception as exc:
                    inc_fs_error("esl_connect")
                    self._last_esl_error = {
                        "kind": self._classify_esl_error(exc),
                        "error": str(exc),
                        "host": self._cfg.esl_host,
                        "port": self._cfg.esl_port,
                        "target": f"{self._cfg.esl_host}:{self._cfg.esl_port}",
                        "dns": diag,
                    }
                    log.error(
                        "freeswitch_gateway.esl_connect_failed",
                        attempt=attempts,
                        error=str(exc),
                        kind=self._last_esl_error["kind"],
                        host=self._cfg.esl_host,
                        port=self._cfg.esl_port,
                        target=self._last_esl_error["target"],
                        dns=diag,
                    )
                    if not self._cfg.esl_reconnect_enabled:
                        raise MediaGatewayNotReadyError(
                            f"ESL connect failed: {exc}"
                        ) from exc
                    if self._cfg.esl_reconnect_max_attempts > 0 and attempts >= self._cfg.esl_reconnect_max_attempts:
                        raise MediaGatewayNotReadyError(
                            f"ESL reconnect attempts exhausted ({attempts})"
                        ) from exc
                    await asyncio.sleep(delay)
                    delay = min(max_delay, delay * 2)

    def _build_esl_client(self) -> FreeSwitchEslClient:
        return FreeSwitchEslClient(
            host=self._cfg.esl_host,
            port=self._cfg.esl_port,
            password=self._cfg.esl_password,
            connect_timeout=float(self._cfg.esl_connect_timeout_seconds),
        )

    async def _run_attach_command(self, uuid_leg: str, rtp_ip: str, rtp_port: int) -> None:
        if self._esl is None:
            raise MediaGatewayNotReadyError("ESL not connected")
        cmd = self._cfg.attach_command_template.format(
            uuid=uuid_leg,
            rtp_ip=rtp_ip,
            rtp_port=rtp_port,
        )
        await self._esl.send_bgapi_nowait(cmd)

    async def _run_hangup_command(self, uuid_leg: str) -> None:
        if self._esl is None:
            return
        cmd = self._cfg.hangup_command_template.format(uuid=uuid_leg)
        try:
            await self._esl.send_bgapi_nowait(cmd)
        except Exception as exc:
            log.warning("freeswitch_gateway.hangup_command_failed", uuid=uuid_leg, error=str(exc))

    async def _esl_event_loop(self) -> None:
        try:
            while True:
                await self._ensure_esl_connected()
                if self._esl is None:
                    raise MediaGatewayNotReadyError("ESL disconnected")
                frame = await self._esl.read_frame()
                ctype = frame.headers.get("Content-Type", "")
                if ctype != "text/event-plain":
                    continue
                event = _parse_esl_event_body(frame.body)
                await self._process_plain_event(event)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            inc_fs_error("esl_event_loop")
            log.error("freeswitch_gateway.esl_event_loop_failed", error=str(exc))
            await self._handle_esl_disconnect()
            if self._cfg.esl_reconnect_enabled:
                self._esl_reader_task = asyncio.create_task(
                    self._esl_event_loop(),
                    name="freeswitch_esl_event_loop_reconnect",
                )

    async def _handle_esl_disconnect(self) -> None:
        esl = self._esl
        self._esl = None
        if esl is not None:
            try:
                await esl.close()
            except Exception:
                pass

    async def _process_plain_event(self, event: dict[str, str]) -> None:
        name = event.get("Event-Name", "")
        inc_fs_esl_event(name)
        normalized, sid, fs_uuid = self._normalize_esl_event(event)
        if sid is None:
            return
        self._update_correlation(sid, fs_uuid)
        await self._apply_normalized_event(sid, normalized, event)

    def _normalize_esl_event(self, event: dict[str, str]) -> tuple[Optional[str], Optional[str], Optional[str]]:
        name = event.get("Event-Name", "")
        subclass = event.get("Event-Subclass", "")
        fs_uuid = (
            event.get("Unique-ID")
            or event.get("Channel-Call-UUID")
            or event.get("variable_uuid")
        )
        sid = self._uuid_to_session.get(fs_uuid or "")
        if sid is None:
            return None, None, fs_uuid
        if name == "CHANNEL_CREATE":
            return "channel_create", sid, fs_uuid
        if name == "CHANNEL_ANSWER":
            return "channel_answer", sid, fs_uuid
        if name in {"CHANNEL_HANGUP", "CHANNEL_HANGUP_COMPLETE"}:
            return "channel_hangup", sid, fs_uuid
        if name == "PLAYBACK_START":
            return "playback_start", sid, fs_uuid
        if name == "PLAYBACK_STOP":
            return "playback_stop", sid, fs_uuid
        if name == "CHANNEL_BRIDGE":
            return "channel_bridge", sid, fs_uuid
        if name == "CUSTOM" and subclass == "ai::barge_in":
            return "barge_in", sid, fs_uuid
        if name == "HEARTBEAT":
            return "heartbeat", sid, fs_uuid
        return "unmapped", sid, fs_uuid

    def _update_correlation(self, session_id: str, fs_uuid: Optional[str]) -> None:
        if not fs_uuid:
            return
        self._uuid_to_session[fs_uuid] = session_id
        corr = self._correlation.get(session_id)
        if corr is None:
            return
        corr.freeswitch_uuid = fs_uuid
        corr.updated_at = datetime.now(timezone.utc)

    async def _apply_normalized_event(
        self,
        session_id: str,
        normalized: Optional[str],
        raw_event: dict[str, str],
    ) -> None:
        if normalized is None:
            return
        state = self._lifecycle.get(session_id)
        if state is None:
            return
        corr = self._correlation.get(session_id)
        mango_leg_id = corr.mango_leg_id if corr else None
        self._try_set_remote_addr_from_event(session_id, raw_event)
        state.last_event = normalized
        state.updated_at = datetime.now(timezone.utc)
        log.info(
            "freeswitch_gateway.event",
            session_id=session_id,
            fs_event=normalized,
            fs_uuid=raw_event.get("Unique-ID"),
        )

        if normalized == "channel_create":
            state.created = True
            if mango_leg_id:
                await self._corr.set_freeswitch_state(
                    mango_leg_id=mango_leg_id,
                    state=TelephonyLegState.INITIATING,
                    freeswitch_uuid=raw_event.get("Unique-ID"),
                    freeswitch_session_id=session_id,
                    raw_event=raw_event,
                )
        elif normalized == "channel_answer":
            state.answered = True
            if mango_leg_id:
                await self._corr.set_freeswitch_state(
                    mango_leg_id=mango_leg_id,
                    state=TelephonyLegState.ANSWERED,
                    freeswitch_uuid=raw_event.get("Unique-ID"),
                    freeswitch_session_id=session_id,
                    raw_event=raw_event,
                )
        elif normalized == "channel_bridge":
            state.bridged = True
            if mango_leg_id:
                await self._corr.set_freeswitch_state(
                    mango_leg_id=mango_leg_id,
                    state=TelephonyLegState.BRIDGED,
                    freeswitch_uuid=raw_event.get("Unique-ID"),
                    freeswitch_session_id=session_id,
                    raw_event=raw_event,
                )
        elif normalized == "playback_start":
            state.playback_active = True
        elif normalized == "playback_stop":
            state.playback_active = False
        elif normalized == "channel_hangup":
            state.hungup = True
            stats = self._media_stats.get(session_id)
            if stats is not None and stats.disconnect_reason is None:
                stats.disconnect_reason = raw_event.get("Hangup-Cause") or "hangup"
            if mango_leg_id:
                await self._corr.set_freeswitch_state(
                    mango_leg_id=mango_leg_id,
                    state=TelephonyLegState.TERMINATED,
                    freeswitch_uuid=raw_event.get("Unique-ID"),
                    freeswitch_session_id=session_id,
                    raw_event=raw_event,
                )
            await self._queue_event(
                session_id,
                MediaEvent(
                    type=MediaEventType.HANGUP,
                    reason=raw_event.get("Hangup-Cause"),
                    payload={"normalized_event": normalized, "raw": raw_event},
                )
            )
            return
        elif normalized == "barge_in":
            await self._queue_event(
                session_id,
                MediaEvent(
                    type=MediaEventType.BARGE_IN,
                    payload={"normalized_event": normalized, "raw": raw_event},
                )
            )
            return

        await self._queue_event(
            session_id,
            MediaEvent(
                type=MediaEventType.HEARTBEAT,
                payload={"normalized_event": normalized, "raw": raw_event},
            )
        )

    async def _open_rtp_runtime(self, session_id: str) -> _RtpRuntime:
        loop = asyncio.get_running_loop()
        protocol = _RtpProtocol(self, session_id)
        bind_port = await self._find_free_udp_port(self._cfg.rtp_port_start, self._cfg.rtp_port_end)
        transport, _ = await loop.create_datagram_endpoint(
            lambda: protocol,
            local_addr=(self._cfg.rtp_ip, bind_port),
        )
        return _RtpRuntime(
            transport=transport,  # type: ignore[arg-type]
            local_ip=self._cfg.rtp_ip,
            local_port=bind_port,
            seq=random.randint(0, 65535),
            ts=random.randint(0, 2**31),
            ssrc=random.randint(1, 2**31 - 1),
        )

    async def _find_free_udp_port(self, start: int, end: int) -> int:
        for port in range(start, end + 1, 2):
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                try:
                    s.bind((self._cfg.rtp_ip, port))
                    return port
                except OSError:
                    continue
        raise MediaGatewayNotReadyError("No free RTP UDP port in configured range.")

    async def _on_rtp_datagram(self, session_id: str, data: bytes, addr: tuple[str, int]) -> None:
        runtime = self._rtp.get(session_id)
        if runtime is None:
            return
        runtime.remote_addr = addr
        self._flush_pending_audio(session_id, runtime)
        pt = _extract_rtp_payload_type(data)
        payload = _extract_rtp_payload(data)
        if not payload:
            return
        decoded = _decode_inbound_audio(payload, inbound_codec=self._cfg.rtp_inbound_codec, payload_type=pt)
        stats = self._media_stats.get(session_id)
        if stats is not None:
            stats.frames_in += 1
            stats.bytes_in += len(decoded)
            stats.last_rtp_at = datetime.now(timezone.utc)
        inc_fs_rtp_in(len(decoded), self._cfg.mode)
        await self._queue_event(session_id, MediaEvent(type=MediaEventType.AUDIO_IN, pcm=decoded))


class _RtpProtocol(asyncio.DatagramProtocol):
    def __init__(self, gateway: FreeSwitchMediaGateway, session_id: str) -> None:
        self._gateway = gateway
        self._session_id = session_id

    def datagram_received(self, data: bytes, addr) -> None:
        asyncio.create_task(self._gateway._on_rtp_datagram(self._session_id, data, addr))


def _parse_esl_event_body(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in body.splitlines():
        if ":" not in raw:
            continue
        k, v = raw.split(":", 1)
        out[k.strip()] = v.strip()
    return out


def _extract_rtp_payload(packet: bytes) -> bytes:
    if len(packet) <= 12:
        return b""
    # Basic RTP v2 header check.
    version = packet[0] >> 6
    if version != 2:
        return b""
    csrc_count = packet[0] & 0x0F
    header_len = 12 + (csrc_count * 4)
    if len(packet) <= header_len:
        return b""
    return packet[header_len:]


def _extract_rtp_payload_type(packet: bytes) -> Optional[int]:
    if len(packet) < 2:
        return None
    version = packet[0] >> 6
    if version != 2:
        return None
    return packet[1] & 0x7F


def _decode_inbound_audio(payload: bytes, *, inbound_codec: str, payload_type: Optional[int]) -> bytes:
    codec = (inbound_codec or "pcm16").lower().strip()
    if codec == "pcmu" or payload_type == 0:
        return _ulaw_bytes_to_pcm16(payload)
    return payload


def _encode_outbound_audio(pcm: bytes, outbound_codec: str) -> bytes:
    codec = (outbound_codec or "pcm16").lower().strip()
    if codec == "pcmu":
        return _pcm16_to_ulaw_bytes(pcm)
    return pcm


def _ulaw_bytes_to_pcm16(data: bytes) -> bytes:
    out = bytearray(len(data) * 2)
    i = 0
    for b in data:
        s = _ulaw_to_linear_sample(b)
        out[i] = s & 0xFF
        out[i + 1] = (s >> 8) & 0xFF
        i += 2
    return bytes(out)


def _pcm16_to_ulaw_bytes(pcm: bytes) -> bytes:
    if len(pcm) % 2 != 0:
        pcm = pcm[:-1]
    out = bytearray(len(pcm) // 2)
    j = 0
    for i in range(0, len(pcm), 2):
        s = int.from_bytes(pcm[i:i + 2], byteorder="little", signed=True)
        out[j] = _linear_to_ulaw_sample(s)
        j += 1
    return bytes(out)


def _ulaw_to_linear_sample(u_val: int) -> int:
    u_val = (~u_val) & 0xFF
    t = ((u_val & 0x0F) << 3) + 0x84
    t <<= (u_val & 0x70) >> 4
    if u_val & 0x80:
        return 0x84 - t
    return t - 0x84


def _linear_to_ulaw_sample(sample: int) -> int:
    clip = 32635
    bias = 0x84
    sign = 0
    if sample < 0:
        sample = -sample
        sign = 0x80
    if sample > clip:
        sample = clip
    sample = sample + bias

    exponent = 7
    mask = 0x4000
    while exponent > 0 and not (sample & mask):
        exponent -= 1
        mask >>= 1
    mantissa = (sample >> (exponent + 3)) & 0x0F
    ulaw = ~(sign | (exponent << 4) | mantissa) & 0xFF
    return ulaw


def _build_rtp_packet(
    *,
    pcm: bytes,
    payload_type: int,
    seq: int,
    ts: int,
    ssrc: int,
) -> bytes:
    b0 = 0x80  # RTP v2, no padding/ext, no CSRC
    b1 = payload_type & 0x7F
    header = struct.pack("!BBHII", b0, b1, seq, ts, ssrc)
    return header + pcm

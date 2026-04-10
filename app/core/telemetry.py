from __future__ import annotations

from typing import Tuple

from app.core.config import settings

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )

    _PROM_AVAILABLE = True
except Exception:  # pragma: no cover - dependency may be unavailable in minimal env
    _PROM_AVAILABLE = False


if _PROM_AVAILABLE:
    _registry = CollectorRegistry()

    # FreeSWITCH / media gateway telemetry
    _fs_session_attach_total = Counter(
        "freeswitch_session_attach_total",
        "Total media session attach attempts",
        ["mode"],
        registry=_registry,
    )
    _fs_session_detach_total = Counter(
        "freeswitch_session_detach_total",
        "Total media session detach operations",
        ["mode"],
        registry=_registry,
    )
    _fs_active_sessions = Gauge(
        "freeswitch_active_sessions",
        "Active FreeSWITCH media sessions",
        ["mode"],
        registry=_registry,
    )
    _fs_rtp_in_packets_total = Counter(
        "freeswitch_rtp_in_packets_total",
        "Inbound RTP packets received from FreeSWITCH leg",
        ["mode"],
        registry=_registry,
    )
    _fs_rtp_in_bytes_total = Counter(
        "freeswitch_rtp_in_bytes_total",
        "Inbound RTP payload bytes received from FreeSWITCH leg",
        ["mode"],
        registry=_registry,
    )
    _fs_rtp_out_packets_total = Counter(
        "freeswitch_rtp_out_packets_total",
        "Outbound RTP packets sent to FreeSWITCH leg",
        ["mode"],
        registry=_registry,
    )
    _fs_rtp_out_bytes_total = Counter(
        "freeswitch_rtp_out_bytes_total",
        "Outbound RTP payload bytes sent to FreeSWITCH leg",
        ["mode"],
        registry=_registry,
    )
    _fs_esl_events_total = Counter(
        "freeswitch_esl_events_total",
        "FreeSWITCH ESL events consumed by event loop",
        ["event_name"],
        registry=_registry,
    )
    _fs_errors_total = Counter(
        "freeswitch_errors_total",
        "FreeSWITCH media gateway errors",
        ["stage"],
        registry=_registry,
    )

    # Direct session audio telemetry
    _direct_sessions_started_total = Counter(
        "direct_sessions_started_total",
        "Direct sessions started",
        ["mode"],
        registry=_registry,
    )
    _direct_sessions_terminated_total = Counter(
        "direct_sessions_terminated_total",
        "Direct sessions terminated",
        ["mode"],
        registry=_registry,
    )
    _direct_audio_in_chunks_total = Counter(
        "direct_audio_in_chunks_total",
        "Direct inbound audio chunk counters",
        ["result"],
        registry=_registry,
    )
    _direct_audio_out_chunks_total = Counter(
        "direct_audio_out_chunks_total",
        "Direct outbound audio chunk counters",
        ["result", "source"],
        registry=_registry,
    )
    _direct_inbound_audio_latency_ms = Histogram(
        "direct_inbound_audio_latency_ms",
        "Latency from inbound queue enqueue to send_audio(model)",
        buckets=(5, 10, 20, 40, 80, 120, 200, 400, 800, 1600, 3200),
        registry=_registry,
    )
    _direct_model_response_latency_ms = Histogram(
        "direct_model_response_latency_ms",
        "Latency from last inbound audio chunk sent to first assistant text/audio callback",
        buckets=(20, 40, 80, 120, 200, 400, 800, 1200, 2000, 4000, 8000),
        registry=_registry,
    )
    _direct_tts_latency_ms = Histogram(
        "direct_tts_latency_ms",
        "Latency from TTS start to first audio chunk produced",
        buckets=(20, 40, 80, 120, 200, 400, 800, 1200, 2000, 4000, 8000),
        registry=_registry,
    )
    _direct_outbound_playback_latency_ms = Histogram(
        "direct_outbound_playback_latency_ms",
        "Latency from outbound queue enqueue to bridge playback",
        buckets=(5, 10, 20, 40, 80, 120, 200, 400, 800, 1600, 3200),
        registry=_registry,
    )


def is_metrics_enabled() -> bool:
    return bool(settings.metrics_enabled and _PROM_AVAILABLE)


def render_metrics() -> Tuple[bytes, str]:
    if not is_metrics_enabled():
        return b"# metrics_disabled_or_prometheus_unavailable\n", "text/plain; version=0.0.4"
    return generate_latest(_registry), CONTENT_TYPE_LATEST


def inc_fs_session_attach(mode: str) -> None:
    if is_metrics_enabled():
        _fs_session_attach_total.labels(mode=mode).inc()


def inc_fs_session_detach(mode: str) -> None:
    if is_metrics_enabled():
        _fs_session_detach_total.labels(mode=mode).inc()


def set_fs_active_sessions(mode: str, value: int) -> None:
    if is_metrics_enabled():
        _fs_active_sessions.labels(mode=mode).set(max(value, 0))


def inc_fs_rtp_in(payload_bytes: int, mode: str) -> None:
    if is_metrics_enabled():
        _fs_rtp_in_packets_total.labels(mode=mode).inc()
        _fs_rtp_in_bytes_total.labels(mode=mode).inc(max(payload_bytes, 0))


def inc_fs_rtp_out(payload_bytes: int, mode: str) -> None:
    if is_metrics_enabled():
        _fs_rtp_out_packets_total.labels(mode=mode).inc()
        _fs_rtp_out_bytes_total.labels(mode=mode).inc(max(payload_bytes, 0))


def inc_fs_esl_event(event_name: str) -> None:
    if is_metrics_enabled():
        _fs_esl_events_total.labels(event_name=event_name or "unknown").inc()


def inc_fs_error(stage: str) -> None:
    if is_metrics_enabled():
        _fs_errors_total.labels(stage=stage or "unknown").inc()


def inc_direct_session_started(mode: str) -> None:
    if is_metrics_enabled():
        _direct_sessions_started_total.labels(mode=mode).inc()


def inc_direct_session_terminated(mode: str) -> None:
    if is_metrics_enabled():
        _direct_sessions_terminated_total.labels(mode=mode).inc()


def inc_direct_audio_in(result: str) -> None:
    if is_metrics_enabled():
        _direct_audio_in_chunks_total.labels(result=result).inc()


def inc_direct_audio_out(result: str, source: str = "unknown") -> None:
    if is_metrics_enabled():
        _direct_audio_out_chunks_total.labels(result=result, source=source).inc()


def observe_direct_inbound_audio_latency(ms: float) -> None:
    if is_metrics_enabled() and ms >= 0:
        _direct_inbound_audio_latency_ms.observe(ms)


def observe_direct_model_response_latency(ms: float) -> None:
    if is_metrics_enabled() and ms >= 0:
        _direct_model_response_latency_ms.observe(ms)


def observe_direct_tts_latency(ms: float) -> None:
    if is_metrics_enabled() and ms >= 0:
        _direct_tts_latency_ms.observe(ms)


def observe_direct_outbound_playback_latency(ms: float) -> None:
    if is_metrics_enabled() and ms >= 0:
        _direct_outbound_playback_latency_ms.observe(ms)

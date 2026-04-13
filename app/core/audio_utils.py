from __future__ import annotations

import math
import time
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from app.core.config import settings

PCM16_SAMPLE_RATE = 16000
PCM16_CHANNELS = 1
PCM16_SAMPLE_WIDTH_BITS = 16
PCM16_ENDIAN = "little"
PCM16_ENCODING = "pcm_s16le"
PCM16_CONTAINER = "raw"
PCM16_BYTES_PER_SAMPLE = 2
PCM16_BYTES_PER_SECOND = PCM16_SAMPLE_RATE * PCM16_CHANNELS * PCM16_BYTES_PER_SAMPLE
PCM16_ANALYSIS_FRAME_MS = 20
PCM16_ANALYSIS_FRAME_BYTES = int(PCM16_BYTES_PER_SECOND * (PCM16_ANALYSIS_FRAME_MS / 1000))
PCM16_SILENT_RMS_THRESHOLD = 0.0015
PCM16_SILENT_PEAK_THRESHOLD = 0.01
PCM16_NEAR_SILENT_RMS_THRESHOLD = 0.008
PCM16_NEAR_SILENT_PEAK_THRESHOLD = 0.05
PCM16_VOICED_SAMPLE_THRESHOLD = 800


@dataclass
class Pcm16ChunkAligner:
    carry: bytes = b""
    raw_bytes: int = 0
    aligned_bytes: int = 0
    odd_chunks: int = 0
    chunks_seen: int = 0

    def push(self, chunk: bytes) -> bytes:
        if not chunk:
            return b""
        self.chunks_seen += 1
        self.raw_bytes += len(chunk)
        if len(chunk) % PCM16_BYTES_PER_SAMPLE != 0:
            self.odd_chunks += 1
        merged = self.carry + chunk
        even_len = len(merged) - (len(merged) % PCM16_BYTES_PER_SAMPLE)
        aligned = merged[:even_len]
        self.carry = merged[even_len:]
        self.aligned_bytes += len(aligned)
        return aligned

    def flush(self, *, pad_final_byte: bool = True) -> bytes:
        if not self.carry:
            return b""
        if not pad_final_byte:
            remainder = self.carry
            self.carry = b""
            return remainder
        padded = self.carry + b"\x00"
        self.carry = b""
        self.aligned_bytes += len(padded)
        return padded


@dataclass(frozen=True)
class Pcm16ChunkingTelemetry:
    chunks_in: int
    chunks_out: int
    bytes_in: int
    bytes_out: int
    tiny_chunks_in: int
    leading_silence_trimmed_ms: float
    trailing_silence_trimmed_ms: float
    trailing_silence_kept_ms: float
    emitted_audio_duration_ms: float
    leading_silence_frames_dropped: int
    trailing_silence_frames_dropped: int
    trailing_silence_frames_kept: int
    silence_chunk_ratio: float


@dataclass(frozen=True)
class Pcm16AudibilityAnalysis:
    silence_class: str
    first_voiced_sample_index: Optional[int]
    first_voiced_offset_ms: Optional[float]
    rms: float
    peak: float
    silence_ratio: float
    sample_count: int


@dataclass(frozen=True)
class Pcm16VoicedFirstEvent:
    chunk_index: int
    silence_class: str
    first_voiced_offset_ms: Optional[float]
    leading_trimmed_ms: float
    total_leading_trimmed_ms: float
    emitted_bytes: int
    dropped_chunks: int
    silent_chunks_dropped: int
    near_silent_chunks_dropped: int


@dataclass(frozen=True)
class Pcm16VoicedFirstTelemetry:
    leading_silence_trimmed_ms: float
    dropped_chunks: int
    silent_chunks_dropped: int
    near_silent_chunks_dropped: int
    first_voiced_offset_ms: Optional[float]
    first_voiced_chunk_index: Optional[int]


def analyze_pcm16_audibility(
    pcm: bytes,
    *,
    sample_rate: int = PCM16_SAMPLE_RATE,
    silent_rms_threshold: float = PCM16_SILENT_RMS_THRESHOLD,
    silent_peak_threshold: float = PCM16_SILENT_PEAK_THRESHOLD,
    near_silent_rms_threshold: float = PCM16_NEAR_SILENT_RMS_THRESHOLD,
    near_silent_peak_threshold: float = PCM16_NEAR_SILENT_PEAK_THRESHOLD,
    sample_amplitude_threshold: int = PCM16_VOICED_SAMPLE_THRESHOLD,
) -> Pcm16AudibilityAnalysis:
    usable = pcm if len(pcm) % PCM16_BYTES_PER_SAMPLE == 0 else pcm[:-1]
    stats = pcm16le_stats(usable)
    first_voiced_sample_index: Optional[int] = None

    for index in range(0, len(usable), PCM16_BYTES_PER_SAMPLE):
        sample = int.from_bytes(
            usable[index:index + PCM16_BYTES_PER_SAMPLE],
            byteorder=PCM16_ENDIAN,
            signed=True,
        )
        if abs(sample) >= sample_amplitude_threshold:
            first_voiced_sample_index = index // PCM16_BYTES_PER_SAMPLE
            break

    if first_voiced_sample_index is None:
        if stats["rms"] <= silent_rms_threshold and stats["peak"] <= silent_peak_threshold:
            silence_class = "silent"
        else:
            silence_class = "near_silent"
    elif stats["rms"] <= near_silent_rms_threshold and stats["peak"] <= near_silent_peak_threshold:
        silence_class = "near_silent"
    else:
        silence_class = "voiced"

    first_voiced_offset_ms = (
        round((first_voiced_sample_index / sample_rate) * 1000.0, 2)
        if first_voiced_sample_index is not None
        else None
    )
    return Pcm16AudibilityAnalysis(
        silence_class=silence_class,
        first_voiced_sample_index=first_voiced_sample_index,
        first_voiced_offset_ms=first_voiced_offset_ms,
        rms=stats["rms"],
        peak=stats["peak"],
        silence_ratio=stats["silence_ratio"],
        sample_count=stats["sample_count"],
    )


def trim_pcm16_to_first_voiced(
    pcm: bytes,
    *,
    sample_rate: int = PCM16_SAMPLE_RATE,
    preserve_ms: float = 2.0,
    fade_in_ms: float = 2.0,
    sample_amplitude_threshold: int = PCM16_VOICED_SAMPLE_THRESHOLD,
) -> tuple[bytes, float, Pcm16AudibilityAnalysis]:
    usable = pcm if len(pcm) % PCM16_BYTES_PER_SAMPLE == 0 else pcm[:-1]
    analysis = analyze_pcm16_audibility(
        usable,
        sample_rate=sample_rate,
        sample_amplitude_threshold=sample_amplitude_threshold,
    )
    if analysis.first_voiced_sample_index is None:
        return b"", pcm16_duration_ms_for_bytes(len(usable)), analysis

    preserve_samples = max(0, int(round((preserve_ms / 1000.0) * sample_rate)))
    trim_samples = max(0, analysis.first_voiced_sample_index - preserve_samples)
    if trim_samples == 0:
        return usable, 0.0, analysis

    trimmed = usable[trim_samples * PCM16_BYTES_PER_SAMPLE:]
    trimmed_ms = round((trim_samples / sample_rate) * 1000.0, 2)
    if fade_in_ms > 0 and trimmed:
        trimmed = apply_pcm16_fade_in(
            trimmed,
            sample_rate=sample_rate,
            fade_in_ms=fade_in_ms,
        )
    return trimmed, trimmed_ms, analysis


def apply_pcm16_fade_in(
    pcm: bytes,
    *,
    sample_rate: int = PCM16_SAMPLE_RATE,
    fade_in_ms: float = 2.0,
) -> bytes:
    usable = pcm if len(pcm) % PCM16_BYTES_PER_SAMPLE == 0 else pcm[:-1]
    if not usable or fade_in_ms <= 0:
        return usable
    fade_samples = max(1, int(round((fade_in_ms / 1000.0) * sample_rate)))
    samples = array("h")
    samples.frombytes(usable)
    fade_samples = min(fade_samples, len(samples))
    if fade_samples <= 1:
        return usable
    for index in range(fade_samples):
        scale = index / max(1, fade_samples - 1)
        samples[index] = int(samples[index] * scale)
    return samples.tobytes()


class Pcm16VoicedFirstGate:
    """
    Startup gate for perceptual latency:
    - drops clearly silent / near-silent startup chunks
    - trims startup silence inside the first voiced chunk
    - applies a tiny fade-in after trimming to avoid clicks
    """

    def __init__(
        self,
        *,
        max_leading_silence_ms: float = 320.0,
        preserve_ms: float = 2.0,
        fade_in_ms: float = 2.0,
        sample_amplitude_threshold: int = PCM16_VOICED_SAMPLE_THRESHOLD,
    ) -> None:
        self._max_leading_silence_ms = max_leading_silence_ms
        self._preserve_ms = preserve_ms
        self._fade_in_ms = fade_in_ms
        self._sample_amplitude_threshold = sample_amplitude_threshold
        self._started = False
        self._chunk_index = 0
        self._leading_silence_trimmed_ms = 0.0
        self._dropped_chunks = 0
        self._silent_chunks_dropped = 0
        self._near_silent_chunks_dropped = 0
        self._first_voiced_offset_ms: Optional[float] = None
        self._first_voiced_chunk_index: Optional[int] = None

    def push(self, chunk: bytes) -> tuple[list[bytes], Optional[Pcm16VoicedFirstEvent]]:
        if not chunk:
            return [], None
        self._chunk_index += 1
        if self._started:
            return [chunk], None

        analysis = analyze_pcm16_audibility(
            chunk,
            sample_amplitude_threshold=self._sample_amplitude_threshold,
        )
        chunk_duration_ms = pcm16_duration_ms_for_bytes(len(chunk))

        if (
            analysis.first_voiced_sample_index is None
            and self._leading_silence_trimmed_ms + chunk_duration_ms <= self._max_leading_silence_ms
        ):
            self._leading_silence_trimmed_ms = round(
                self._leading_silence_trimmed_ms + chunk_duration_ms,
                2,
            )
            self._dropped_chunks += 1
            if analysis.silence_class == "silent":
                self._silent_chunks_dropped += 1
            else:
                self._near_silent_chunks_dropped += 1
            return [], None

        trimmed_chunk = chunk
        leading_trimmed_ms = 0.0
        if analysis.first_voiced_sample_index is not None:
            trimmed_chunk, leading_trimmed_ms, analysis = trim_pcm16_to_first_voiced(
                chunk,
                preserve_ms=self._preserve_ms,
                fade_in_ms=self._fade_in_ms,
                sample_amplitude_threshold=self._sample_amplitude_threshold,
            )
            self._first_voiced_offset_ms = analysis.first_voiced_offset_ms
            self._first_voiced_chunk_index = self._chunk_index

        self._started = True
        self._leading_silence_trimmed_ms = round(
            self._leading_silence_trimmed_ms + leading_trimmed_ms,
            2,
        )
        emitted = trimmed_chunk if trimmed_chunk else chunk
        event = Pcm16VoicedFirstEvent(
            chunk_index=self._chunk_index,
            silence_class=analysis.silence_class,
            first_voiced_offset_ms=analysis.first_voiced_offset_ms,
            leading_trimmed_ms=leading_trimmed_ms,
            total_leading_trimmed_ms=self._leading_silence_trimmed_ms,
            emitted_bytes=len(emitted),
            dropped_chunks=self._dropped_chunks,
            silent_chunks_dropped=self._silent_chunks_dropped,
            near_silent_chunks_dropped=self._near_silent_chunks_dropped,
        )
        return [emitted], event

    def snapshot(self) -> Pcm16VoicedFirstTelemetry:
        return Pcm16VoicedFirstTelemetry(
            leading_silence_trimmed_ms=self._leading_silence_trimmed_ms,
            dropped_chunks=self._dropped_chunks,
            silent_chunks_dropped=self._silent_chunks_dropped,
            near_silent_chunks_dropped=self._near_silent_chunks_dropped,
            first_voiced_offset_ms=self._first_voiced_offset_ms,
            first_voiced_chunk_index=self._first_voiced_chunk_index,
        )


class Pcm16RealtimeOptimizer:
    """
    Shapes a PCM16 TTS stream for realtime playback.

    Goals:
    - remove obvious leading silence that delays the perceived response start
    - suppress excessive trailing silence that extends the drain tail
    - coalesce over-fragmented provider chunks into practical websocket frames
    - keep the first emitted chunk small enough for fast startup
    """

    def __init__(
        self,
        *,
        analysis_frame_bytes: int = PCM16_ANALYSIS_FRAME_BYTES,
        startup_target_ms: int = 20,
        steady_target_ms: int = 80,
        max_leading_silence_ms: int = 120,
        keep_trailing_silence_ms: int = 60,
        silence_ratio_threshold: float = 0.995,
        rms_threshold: float = 0.003,
    ) -> None:
        self._analysis_frame_bytes = analysis_frame_bytes
        self._startup_target_bytes = pcm16_bytes_for_duration_ms(startup_target_ms)
        self._steady_target_bytes = pcm16_bytes_for_duration_ms(steady_target_ms)
        self._max_leading_silence_frames = max(0, max_leading_silence_ms // PCM16_ANALYSIS_FRAME_MS)
        self._keep_trailing_silence_frames = max(0, keep_trailing_silence_ms // PCM16_ANALYSIS_FRAME_MS)
        self._silence_ratio_threshold = silence_ratio_threshold
        self._rms_threshold = rms_threshold

        self._frame_carry = bytearray()
        self._emit_buffer = bytearray()
        self._pending_trailing_frames: list[bytes] = []
        self._speech_started = False

        self._chunks_in = 0
        self._chunks_out = 0
        self._bytes_in = 0
        self._bytes_out = 0
        self._tiny_chunks_in = 0
        self._silent_like_frames_seen = 0
        self._leading_silence_frames_dropped = 0
        self._trailing_silence_frames_dropped = 0
        self._trailing_silence_frames_kept = 0

    def push(self, chunk: bytes) -> list[bytes]:
        if not chunk:
            return []
        self._chunks_in += 1
        self._bytes_in += len(chunk)
        if len(chunk) < self._analysis_frame_bytes:
            self._tiny_chunks_in += 1

        self._frame_carry.extend(chunk)
        emitted: list[bytes] = []

        while len(self._frame_carry) >= self._analysis_frame_bytes:
            frame = bytes(self._frame_carry[:self._analysis_frame_bytes])
            del self._frame_carry[:self._analysis_frame_bytes]
            emitted.extend(self._process_frame(frame))

        return emitted

    def flush(self) -> tuple[list[bytes], Pcm16ChunkingTelemetry]:
        emitted: list[bytes] = []
        if self._frame_carry:
            padded = bytes(self._frame_carry)
            if len(padded) % PCM16_BYTES_PER_SAMPLE:
                padded += b"\x00"
            self._frame_carry.clear()
            emitted.extend(self._process_frame(padded))

        if self._pending_trailing_frames:
            keep = self._pending_trailing_frames[:self._keep_trailing_silence_frames]
            drop = self._pending_trailing_frames[self._keep_trailing_silence_frames:]
            self._trailing_silence_frames_kept += len(keep)
            self._trailing_silence_frames_dropped += len(drop)
            for frame in keep:
                self._append_emit_bytes(frame, emitted)
            self._pending_trailing_frames.clear()

        if self._emit_buffer:
            emitted.append(bytes(self._emit_buffer))
            self._record_emission(len(self._emit_buffer))
            self._emit_buffer.clear()

        telemetry = Pcm16ChunkingTelemetry(
            chunks_in=self._chunks_in,
            chunks_out=self._chunks_out,
            bytes_in=self._bytes_in,
            bytes_out=self._bytes_out,
            tiny_chunks_in=self._tiny_chunks_in,
            leading_silence_trimmed_ms=self._leading_silence_frames_dropped * PCM16_ANALYSIS_FRAME_MS,
            trailing_silence_trimmed_ms=self._trailing_silence_frames_dropped * PCM16_ANALYSIS_FRAME_MS,
            trailing_silence_kept_ms=self._trailing_silence_frames_kept * PCM16_ANALYSIS_FRAME_MS,
            emitted_audio_duration_ms=pcm16_duration_ms_for_bytes(self._bytes_out),
            leading_silence_frames_dropped=self._leading_silence_frames_dropped,
            trailing_silence_frames_dropped=self._trailing_silence_frames_dropped,
            trailing_silence_frames_kept=self._trailing_silence_frames_kept,
            silence_chunk_ratio=(
                self._silent_like_frames_seen
                / max(1, self._silent_like_frames_seen + (self._bytes_out // max(1, self._analysis_frame_bytes)))
            ),
        )
        return emitted, telemetry

    def _process_frame(self, frame: bytes) -> list[bytes]:
        emitted: list[bytes] = []
        stats = pcm16le_stats(frame)
        is_silent = (
            stats["silence_ratio"] >= self._silence_ratio_threshold
            or stats["rms"] <= self._rms_threshold
        )
        if is_silent:
            self._silent_like_frames_seen += 1

        if not self._speech_started:
            if is_silent and self._leading_silence_frames_dropped < self._max_leading_silence_frames:
                self._leading_silence_frames_dropped += 1
                return emitted
            if not is_silent:
                self._speech_started = True

        if self._speech_started and is_silent:
            self._pending_trailing_frames.append(frame)
            return emitted

        if self._pending_trailing_frames:
            for trailing_frame in self._pending_trailing_frames:
                self._append_emit_bytes(trailing_frame, emitted)
            self._pending_trailing_frames.clear()

        self._append_emit_bytes(frame, emitted)
        return emitted

    def _append_emit_bytes(self, frame: bytes, emitted: list[bytes]) -> None:
        self._emit_buffer.extend(frame)
        target = self._startup_target_bytes if self._chunks_out == 0 else self._steady_target_bytes
        while len(self._emit_buffer) >= target:
            chunk = bytes(self._emit_buffer[:target])
            del self._emit_buffer[:target]
            emitted.append(chunk)
            self._record_emission(len(chunk))

    def _record_emission(self, byte_length: int) -> None:
        self._chunks_out += 1
        self._bytes_out += byte_length


def pcm16_bytes_for_duration_ms(duration_ms: float) -> int:
    raw = int(round(PCM16_BYTES_PER_SECOND * (duration_ms / 1000.0)))
    if raw <= 0:
        return PCM16_BYTES_PER_SAMPLE
    remainder = raw % PCM16_BYTES_PER_SAMPLE
    return raw if remainder == 0 else raw + (PCM16_BYTES_PER_SAMPLE - remainder)


def pcm16_duration_ms_for_bytes(byte_length: int) -> float:
    if byte_length <= 0:
        return 0.0
    return round((byte_length / PCM16_BYTES_PER_SECOND) * 1000.0, 2)


def pcm16le_stats(
    pcm: bytes,
    *,
    sample_rate: int = PCM16_SAMPLE_RATE,
    channels: int = PCM16_CHANNELS,
    preview_bytes: int = 12,
    silence_threshold: float = 0.01,
) -> dict[str, Any]:
    usable = pcm if len(pcm) % PCM16_BYTES_PER_SAMPLE == 0 else pcm[:-1]
    sample_count = len(usable) // PCM16_BYTES_PER_SAMPLE
    peak = 0.0
    sum_squares = 0.0
    silent_samples = 0
    clipped_samples = 0

    for index in range(0, len(usable), PCM16_BYTES_PER_SAMPLE):
        sample = int.from_bytes(
            usable[index:index + PCM16_BYTES_PER_SAMPLE],
            byteorder=PCM16_ENDIAN,
            signed=True,
        )
        normalized = sample / 32768.0
        absolute = abs(normalized)
        if absolute <= silence_threshold:
            silent_samples += 1
        if absolute >= 0.999:
            clipped_samples += 1
        if absolute > peak:
            peak = absolute
        sum_squares += normalized * normalized

    rms = math.sqrt(sum_squares / sample_count) if sample_count else 0.0
    silence_ratio = (silent_samples / sample_count) if sample_count else 1.0
    clipping_ratio = (clipped_samples / sample_count) if sample_count else 0.0
    duration_ms = round((sample_count / max(1, channels) / sample_rate) * 1000, 2) if sample_count else 0.0

    return {
        "format": PCM16_ENCODING,
        "sample_rate": sample_rate,
        "channels": channels,
        "sample_width_bits": PCM16_SAMPLE_WIDTH_BITS,
        "container": PCM16_CONTAINER,
        "endian": PCM16_ENDIAN,
        "byte_length": len(pcm),
        "sample_count": sample_count,
        "duration_ms": duration_ms,
        "first_bytes_hex": pcm[:preview_bytes].hex(),
        "rms": round(rms, 6),
        "peak": round(peak, 6),
        "silence_ratio": round(silence_ratio, 6),
        "clipping_ratio": round(clipping_ratio, 6),
        "odd_length": bool(len(pcm) % PCM16_BYTES_PER_SAMPLE),
    }


def dump_pcm16le_wav(
    stage: str,
    pcm: bytes,
    *,
    session_id: Optional[str] = None,
    call_id: Optional[str] = None,
    sample_rate: int = PCM16_SAMPLE_RATE,
    channels: int = PCM16_CHANNELS,
) -> Optional[str]:
    if not settings.audio_debug_dump_enabled:
        return None
    directory = Path(settings.audio_debug_dump_dir).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = int(time.time() * 1000)
    safe_session = (session_id or "no-session").replace("/", "_")
    safe_call = (call_id or "no-call").replace("/", "_")
    path = directory / f"{timestamp}_{safe_call}_{safe_session}_{stage}.wav"
    usable = pcm if len(pcm) % PCM16_BYTES_PER_SAMPLE == 0 else pcm[:-1]
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(PCM16_BYTES_PER_SAMPLE)
        wf.setframerate(sample_rate)
        wf.writeframes(usable)
    return str(path)

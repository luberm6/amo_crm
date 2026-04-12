from __future__ import annotations

import math
import time
import wave
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

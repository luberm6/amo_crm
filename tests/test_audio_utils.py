from __future__ import annotations

from app.core.audio_utils import (
    PCM16_ANALYSIS_FRAME_BYTES,
    Pcm16ChunkAligner,
    Pcm16RealtimeOptimizer,
    pcm16_duration_ms_for_bytes,
    pcm16le_stats,
)


def _pcm_sample(value: int) -> bytes:
    return int(value).to_bytes(2, "little", signed=True)


def _pcm_frame(sample_value: int, byte_length: int = PCM16_ANALYSIS_FRAME_BYTES) -> bytes:
    sample = _pcm_sample(sample_value)
    return sample * (byte_length // 2)


def test_pcm16_chunk_aligner_preserves_sample_boundaries_without_midstream_padding() -> None:
    aligner = Pcm16ChunkAligner()

    first = aligner.push(b"\x01")
    second = aligner.push(b"\x02\x03")
    third = aligner.push(b"\x04\x05\x06")
    tail = aligner.flush()

    assert first == b""
    assert second == b"\x01\x02"
    assert third == b"\x03\x04\x05\x06"
    assert tail == b""
    assert aligner.odd_chunks == 2
    assert aligner.raw_bytes == 6
    assert aligner.aligned_bytes == 6


def test_pcm16le_stats_reports_expected_metadata() -> None:
    pcm = (
        int(0).to_bytes(2, "little", signed=True)
        + int(8192).to_bytes(2, "little", signed=True)
        + int(-8192).to_bytes(2, "little", signed=True)
        + int(32767).to_bytes(2, "little", signed=True)
    )

    stats = pcm16le_stats(pcm)

    assert stats["format"] == "pcm_s16le"
    assert stats["sample_rate"] == 16000
    assert stats["channels"] == 1
    assert stats["sample_width_bits"] == 16
    assert stats["container"] == "raw"
    assert stats["endian"] == "little"
    assert stats["byte_length"] == 8
    assert stats["sample_count"] == 4
    assert stats["first_bytes_hex"] == pcm[:12].hex()
    assert stats["peak"] > 0.9
    assert stats["rms"] > 0.0


def test_pcm16_realtime_optimizer_trims_leading_and_trailing_silence() -> None:
    optimizer = Pcm16RealtimeOptimizer()

    leading_silence = [_pcm_frame(0) for _ in range(6)]
    voiced = [_pcm_frame(9000) for _ in range(2)]
    trailing_silence = [_pcm_frame(0) for _ in range(5)]

    emitted: list[bytes] = []
    for chunk in [*leading_silence, *voiced, *trailing_silence]:
        emitted.extend(optimizer.push(chunk))
    tail, telemetry = optimizer.flush()
    emitted.extend(tail)

    merged = b"".join(emitted)

    assert telemetry.leading_silence_trimmed_ms == 120
    assert telemetry.trailing_silence_trimmed_ms == 40
    assert telemetry.trailing_silence_kept_ms == 60
    assert telemetry.chunks_in == 13
    assert telemetry.chunks_out >= 2
    assert merged.startswith(_pcm_sample(9000))
    assert pcm16_duration_ms_for_bytes(len(merged)) == 100.0


def test_pcm16_realtime_optimizer_coalesces_overfragmented_chunks() -> None:
    optimizer = Pcm16RealtimeOptimizer()
    tiny_chunk = _pcm_sample(7000) * 80  # 160 bytes = 5 ms @ 16 kHz mono PCM16

    emitted: list[bytes] = []
    for _ in range(16):
        emitted.extend(optimizer.push(tiny_chunk))
    tail, telemetry = optimizer.flush()
    emitted.extend(tail)

    assert telemetry.chunks_in == 16
    assert telemetry.tiny_chunks_in == 16
    assert telemetry.chunks_out == 2
    assert [len(chunk) for chunk in emitted] == [640, 1920]
    assert telemetry.bytes_out == 2560

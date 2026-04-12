from __future__ import annotations

from app.core.audio_utils import Pcm16ChunkAligner, pcm16le_stats


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

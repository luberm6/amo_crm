from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import scripts.local_env_doctor as doctor
from scripts.local_env_doctor import (
    DoctorCheck,
    classify_overall_status,
    docker_daemon_reachable,
    is_missing_value,
    parse_database_endpoint,
    parse_env_assignments,
    required_browser_voice_secrets,
)


def test_is_missing_value_recognises_placeholders() -> None:
    assert is_missing_value(None) is True
    assert is_missing_value("") is True
    assert is_missing_value("CHANGE_ME") is True
    assert is_missing_value("CHANGE_ME_NOW") is True
    assert is_missing_value("...") is True
    assert is_missing_value(False) is False
    assert is_missing_value("real-value") is False


def test_classify_overall_status_ignores_non_blocking_checks() -> None:
    checks = [
        DoctorCheck(name="optional", status="PARTIAL", message="", affects_overall=False),
        DoctorCheck(name="main", status="READY", message=""),
    ]
    assert classify_overall_status(checks) == "READY"

    checks.append(DoctorCheck(name="voice", status="PARTIAL", message=""))
    assert classify_overall_status(checks) == "PARTIAL"

    checks.append(DoctorCheck(name="database", status="BLOCKED", message=""))
    assert classify_overall_status(checks) == "BLOCKED"


def test_parse_env_assignments_reads_key_value_lines(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nDATABASE_URL=postgres://example\nEMPTY=\nSPACED = value \nINVALID\n",
        encoding="utf-8",
    )

    values = parse_env_assignments(env_file)

    assert values == {
        "DATABASE_URL": "postgres://example",
        "EMPTY": "",
        "SPACED": "value",
    }


def test_required_browser_voice_secrets_for_tts_primary() -> None:
    settings = SimpleNamespace(
        gemini_api_key="",
        direct_voice_strategy="tts_primary",
        direct_voice_allow_tts_fallback=True,
        elevenlabs_api_key="",
        elevenlabs_voice_id="",
    )

    secrets = required_browser_voice_secrets(settings)

    assert [item["variable"] for item in secrets] == [
        "GEMINI_API_KEY",
        "ELEVENLABS_API_KEY",
        "ELEVENLABS_VOICE_ID",
    ]


def test_required_browser_voice_secrets_for_gemini_primary_without_fallback() -> None:
    settings = SimpleNamespace(
        gemini_api_key="live-key",
        direct_voice_strategy="gemini_primary",
        direct_voice_allow_tts_fallback=False,
        elevenlabs_api_key="",
        elevenlabs_voice_id="",
    )

    secrets = required_browser_voice_secrets(settings)

    assert secrets == []


def test_parse_database_endpoint_reads_host_and_port() -> None:
    host, port = parse_database_endpoint(
        "postgresql+asyncpg://amo_user:amo_pass@127.0.0.1:5433/amo_crm"
    )

    assert host == "127.0.0.1"
    assert port == 5433


def test_docker_daemon_reachable_false_when_docker_binary_is_missing() -> None:
    with patch.object(doctor.shutil, "which", return_value=None):
        assert docker_daemon_reachable() is False

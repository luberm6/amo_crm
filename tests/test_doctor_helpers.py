"""Unit tests for local_env_doctor.py helper functions.

Tests cover:
- is_missing_value: detects empty/placeholder values
- classify_overall_status: READY / PARTIAL / BLOCKED aggregation
- required_browser_voice_secrets: which secrets are needed for browser voice
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Add scripts/ to path so local_env_doctor can be imported without installation
_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from local_env_doctor import (  # noqa: E402
    DoctorCheck,
    classify_overall_status,
    is_missing_value,
    required_browser_voice_secrets,
)


# ---------------------------------------------------------------------------
# is_missing_value
# ---------------------------------------------------------------------------

class TestIsMissingValue:
    def test_empty_string_is_missing(self):
        assert is_missing_value("") is True

    def test_whitespace_only_is_missing(self):
        assert is_missing_value("   ") is True

    def test_none_is_missing(self):
        assert is_missing_value(None) is True

    def test_change_me_is_missing(self):
        assert is_missing_value("CHANGE_ME") is True

    def test_change_me_prefix_is_missing(self):
        assert is_missing_value("CHANGE_ME_TO_SOMETHING") is True

    def test_known_placeholders_are_missing(self):
        for placeholder in (
            "your-token-here",
            "your-secret-here",
            "your-api-key-here",
            "your-telegram-bot-token-here",
            "PUBLIC_OR_REACHABLE_IP",
            "...",
        ):
            assert is_missing_value(placeholder) is True, f"Expected {placeholder!r} to be missing"

    def test_real_value_not_missing(self):
        assert is_missing_value("AIzaSyXXXsomething") is False

    def test_bool_false_not_missing(self):
        # False is a valid configured value (e.g. elevenlabs_enabled=False)
        assert is_missing_value(False) is False

    def test_bool_true_not_missing(self):
        assert is_missing_value(True) is False

    def test_zero_int_not_missing(self):
        # 0 is a valid configured value
        assert is_missing_value(0) is False

    def test_nonzero_int_not_missing(self):
        assert is_missing_value(42) is False


# ---------------------------------------------------------------------------
# classify_overall_status
# ---------------------------------------------------------------------------

class TestClassifyOverallStatus:
    def _check(self, name: str, status: str, affects: bool = True) -> DoctorCheck:
        return DoctorCheck(name=name, status=status, message="msg", affects_overall=affects)

    def test_all_ready_returns_ready(self):
        checks = [
            self._check("a", "READY"),
            self._check("b", "READY"),
        ]
        assert classify_overall_status(checks) == "READY"

    def test_one_partial_returns_partial(self):
        checks = [
            self._check("a", "READY"),
            self._check("b", "PARTIAL"),
        ]
        assert classify_overall_status(checks) == "PARTIAL"

    def test_one_blocked_returns_blocked(self):
        checks = [
            self._check("a", "READY"),
            self._check("b", "BLOCKED"),
        ]
        assert classify_overall_status(checks) == "BLOCKED"

    def test_blocked_overrides_partial(self):
        checks = [
            self._check("a", "PARTIAL"),
            self._check("b", "BLOCKED"),
        ]
        assert classify_overall_status(checks) == "BLOCKED"

    def test_empty_checks_returns_ready(self):
        assert classify_overall_status([]) == "READY"

    def test_affects_overall_false_ignored(self):
        checks = [
            self._check("a", "READY"),
            self._check("b", "BLOCKED", affects=False),  # must be ignored
        ]
        assert classify_overall_status(checks) == "READY"

    def test_affects_overall_false_partial_ignored(self):
        checks = [
            self._check("a", "READY"),
            self._check("b", "PARTIAL", affects=False),
        ]
        assert classify_overall_status(checks) == "READY"

    def test_all_non_affecting_blocked_returns_ready(self):
        checks = [
            self._check("a", "BLOCKED", affects=False),
            self._check("b", "BLOCKED", affects=False),
        ]
        assert classify_overall_status(checks) == "READY"


# ---------------------------------------------------------------------------
# required_browser_voice_secrets
# ---------------------------------------------------------------------------

class _FakeSettings:
    """Minimal fake settings object for testing required_browser_voice_secrets."""

    def __init__(
        self,
        gemini_api_key: str = "",
        direct_voice_strategy: str = "disabled",
        direct_voice_allow_tts_fallback: bool = True,
        elevenlabs_api_key: str = "",
        elevenlabs_voice_id: str = "",
    ):
        self.gemini_api_key = gemini_api_key
        self.direct_voice_strategy = direct_voice_strategy
        self.direct_voice_allow_tts_fallback = direct_voice_allow_tts_fallback
        self.elevenlabs_api_key = elevenlabs_api_key
        self.elevenlabs_voice_id = elevenlabs_voice_id


class TestRequiredBrowserVoiceSecrets:
    def test_missing_gemini_key_appears(self):
        settings = _FakeSettings(gemini_api_key="")
        secrets = required_browser_voice_secrets(settings)
        variables = [s["variable"] for s in secrets]
        assert "GEMINI_API_KEY" in variables

    def test_present_gemini_key_not_in_list(self):
        settings = _FakeSettings(
            gemini_api_key="real-key",
            direct_voice_strategy="disabled",
        )
        secrets = required_browser_voice_secrets(settings)
        variables = [s["variable"] for s in secrets]
        assert "GEMINI_API_KEY" not in variables

    def test_tts_primary_requires_elevenlabs(self):
        settings = _FakeSettings(
            gemini_api_key="real-key",
            direct_voice_strategy="tts_primary",
            elevenlabs_api_key="",
            elevenlabs_voice_id="",
        )
        secrets = required_browser_voice_secrets(settings)
        variables = [s["variable"] for s in secrets]
        assert "ELEVENLABS_API_KEY" in variables
        assert "ELEVENLABS_VOICE_ID" in variables

    def test_tts_primary_all_present_returns_empty(self):
        settings = _FakeSettings(
            gemini_api_key="real-key",
            direct_voice_strategy="tts_primary",
            elevenlabs_api_key="el-key",
            elevenlabs_voice_id="voice-123",
        )
        secrets = required_browser_voice_secrets(settings)
        assert secrets == []

    def test_disabled_strategy_no_elevenlabs_required(self):
        settings = _FakeSettings(
            gemini_api_key="real-key",
            direct_voice_strategy="disabled",
            elevenlabs_api_key="",
            elevenlabs_voice_id="",
        )
        secrets = required_browser_voice_secrets(settings)
        variables = [s["variable"] for s in secrets]
        assert "ELEVENLABS_API_KEY" not in variables
        assert "ELEVENLABS_VOICE_ID" not in variables

    def test_gemini_primary_with_fallback_requires_elevenlabs_when_missing(self):
        settings = _FakeSettings(
            gemini_api_key="real-key",
            direct_voice_strategy="gemini_primary",
            direct_voice_allow_tts_fallback=True,
            elevenlabs_api_key="",
            elevenlabs_voice_id="",
        )
        secrets = required_browser_voice_secrets(settings)
        variables = [s["variable"] for s in secrets]
        assert "ELEVENLABS_API_KEY" in variables

    def test_gemini_primary_no_fallback_no_elevenlabs_required(self):
        settings = _FakeSettings(
            gemini_api_key="real-key",
            direct_voice_strategy="gemini_primary",
            direct_voice_allow_tts_fallback=False,
            elevenlabs_api_key="",
            elevenlabs_voice_id="",
        )
        secrets = required_browser_voice_secrets(settings)
        variables = [s["variable"] for s in secrets]
        assert "ELEVENLABS_API_KEY" not in variables

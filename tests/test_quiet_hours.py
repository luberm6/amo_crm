"""
Tests for quiet hours enforcement.

Covers:
- Disabled by default (no error at any time)
- Enabled: call within window → passes
- Enabled: call outside window → QuietHoursError
- Invalid timezone → enforcement skipped (safe degradation)
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import QuietHoursError
from app.integrations.call_engine.stub import StubEngine
from app.services.call_service import CallService, _check_quiet_hours


# ── _check_quiet_hours unit tests ─────────────────────────────────────────────

def test_quiet_hours_disabled_by_default():
    """_check_quiet_hours does nothing when enforce_quiet_hours=False."""
    import app.core.config as cfg
    with patch.object(cfg.settings, "enforce_quiet_hours", False):
        _check_quiet_hours()  # should not raise


def test_quiet_hours_passes_inside_window():
    """Hour inside [9, 21) → no exception."""
    import app.core.config as cfg
    from datetime import datetime as _dt

    class _FakeDt:
        @staticmethod
        def now(tz=None):
            return _dt(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    with patch.object(cfg.settings, "enforce_quiet_hours", True), \
         patch.object(cfg.settings, "calling_hour_start", 9), \
         patch.object(cfg.settings, "calling_hour_end", 21), \
         patch.object(cfg.settings, "calling_timezone", "UTC"), \
         patch("app.services.call_service.datetime", _FakeDt):
        _check_quiet_hours()  # should not raise


def test_quiet_hours_raises_before_window():
    """Hour before 09:00 → QuietHoursError."""
    import app.core.config as cfg
    from datetime import datetime as _dt

    class _FakeDt:
        @staticmethod
        def now(tz=None):
            return _dt(2026, 1, 1, 3, 0, 0, tzinfo=timezone.utc)

    with patch.object(cfg.settings, "enforce_quiet_hours", True), \
         patch.object(cfg.settings, "calling_hour_start", 9), \
         patch.object(cfg.settings, "calling_hour_end", 21), \
         patch.object(cfg.settings, "calling_timezone", "UTC"), \
         patch("app.services.call_service.datetime", _FakeDt):
        with pytest.raises(QuietHoursError) as exc_info:
            _check_quiet_hours()

    assert exc_info.value.status_code == 422
    assert "allowed" in exc_info.value.message


def test_quiet_hours_raises_after_window():
    """Hour at or after 21:00 → QuietHoursError."""
    import app.core.config as cfg
    from datetime import datetime as _dt

    class _FakeDt:
        @staticmethod
        def now(tz=None):
            return _dt(2026, 1, 1, 21, 30, 0, tzinfo=timezone.utc)

    with patch.object(cfg.settings, "enforce_quiet_hours", True), \
         patch.object(cfg.settings, "calling_hour_start", 9), \
         patch.object(cfg.settings, "calling_hour_end", 21), \
         patch.object(cfg.settings, "calling_timezone", "UTC"), \
         patch("app.services.call_service.datetime", _FakeDt):
        with pytest.raises(QuietHoursError):
            _check_quiet_hours()


def test_quiet_hours_bad_timezone_skips_enforcement():
    """Invalid timezone → enforcement skipped, no crash."""
    import app.core.config as cfg

    with patch.object(cfg.settings, "enforce_quiet_hours", True), \
         patch.object(cfg.settings, "calling_timezone", "Invalid/Zone"):
        _check_quiet_hours()  # should NOT raise — safe degradation


# ── Service integration ───────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_service_create_call_blocked_by_quiet_hours(session: AsyncSession):
    """create_call raises QuietHoursError when outside window."""
    import app.core.config as cfg
    from datetime import datetime as _dt

    class _FakeDt:
        @staticmethod
        def now(tz=None):
            return _dt(2026, 1, 1, 2, 0, 0, tzinfo=timezone.utc)

    svc = CallService(session=session, engine=StubEngine())

    with patch.object(cfg.settings, "enforce_quiet_hours", True), \
         patch.object(cfg.settings, "calling_hour_start", 9), \
         patch.object(cfg.settings, "calling_hour_end", 21), \
         patch.object(cfg.settings, "calling_timezone", "UTC"), \
         patch("app.services.call_service.datetime", _FakeDt):
        with pytest.raises(QuietHoursError):
            await svc.create_call(raw_phone="+79991234567")

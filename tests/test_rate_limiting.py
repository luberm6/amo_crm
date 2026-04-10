"""
Rate limiting and abuse prevention tests.

Tests cover:
- Fixed-window Redis counters (RateLimiter)
- Semantic abuse policy checks (AbusePolicy)
- Fail-open behavior when Redis unavailable
- Middleware IP flood protection
- Bot friendly error messages for 429 responses
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import RateLimitError
from app.core.rate_limit import RateLimiter, AbusePolicy
from app.models.call import Call, CallStatus
from app.repositories.call_repo import CallRepository


# Fixture to enable rate limiting for rate limit tests
@pytest.fixture
def enable_rate_limit():
    """Temporarily enable rate limiting for specific tests."""
    original = settings.rate_limit_enabled
    settings.rate_limit_enabled = True
    yield
    settings.rate_limit_enabled = original


# ── RateLimiter Tests ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fixed_window_allows_under_limit():
    """Fixed-window allows requests under the limit."""
    redis_mock = AsyncMock()
    redis_mock.eval = AsyncMock(side_effect=[1, 2, 3, 4, 5, 6, 7, 8, 9])
    limiter = RateLimiter(redis_mock)

    # 9 calls under limit of 10 should pass
    for i in range(9):
        await limiter.check_fixed_window("test_key", limit=10, window_seconds=60, label="test")

    # All calls should have returned without exception


@pytest.mark.asyncio
async def test_fixed_window_blocks_at_limit():
    """Fixed-window blocks when count exceeds limit."""
    redis_mock = AsyncMock()
    redis_mock.eval = AsyncMock(return_value=11)  # 11th request
    limiter = RateLimiter(redis_mock)

    with pytest.raises(RateLimitError) as exc_info:
        await limiter.check_fixed_window("test_key", limit=10, window_seconds=60, label="test")

    assert exc_info.value.error_code == "rate_limit_exceeded"


@pytest.mark.asyncio
async def test_fixed_window_fail_open_when_no_redis():
    """Fixed-window passes when Redis is None (fail-open)."""
    limiter = RateLimiter(None)

    # Should not raise despite any limit
    for i in range(100):
        await limiter.check_fixed_window("test_key", limit=10, window_seconds=60, label="test")


# ── AbusePolicy Call Create Tests ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_abuse_policy_call_create_passes(session: AsyncSession, enable_rate_limit):
    """AbusePolicy allows call creation when all limits are satisfied."""
    redis_mock = AsyncMock()
    redis_mock.eval = AsyncMock(side_effect=[1, 1])  # Per-key, per-IP
    redis_mock.exists = AsyncMock(return_value=False)  # No cooldown
    redis_mock.setex = AsyncMock()

    policy = AbusePolicy(redis_mock, session)

    # Should pass without exception
    # Use unique phone to avoid hitting daily cap from other tests
    await policy.check_call_create(api_key="test-key", phone="+78881110001", ip="127.0.0.1")

    # setex should have been called to set cooldown
    redis_mock.setex.assert_called_once()


@pytest.mark.asyncio
async def test_abuse_policy_call_create_per_key_limit(session: AsyncSession, enable_rate_limit):
    """AbusePolicy blocks when per-API-key per-minute limit is exceeded."""
    redis_mock = AsyncMock()
    redis_mock.eval = AsyncMock(return_value=11)  # Exceeds limit=10

    policy = AbusePolicy(redis_mock, session)

    with pytest.raises(RateLimitError) as exc_info:
        await policy.check_call_create(api_key="test-key", phone="+79991234567", ip="127.0.0.1")

    assert "Rate limit exceeded" in str(exc_info.value)


@pytest.mark.asyncio
async def test_abuse_policy_phone_cooldown_blocks(session: AsyncSession, enable_rate_limit):
    """AbusePolicy blocks when phone cooldown TTL exists."""
    redis_mock = AsyncMock()
    redis_mock.eval = AsyncMock(side_effect=[1, 1])  # Per-key, per-IP pass
    redis_mock.exists = AsyncMock(return_value=True)  # Cooldown exists

    policy = AbusePolicy(redis_mock, session)

    with pytest.raises(RateLimitError) as exc_info:
        await policy.check_call_create(api_key="test-key", phone="+79991234567", ip="127.0.0.1")

    assert "cannot be called again so soon" in str(exc_info.value)


@pytest.mark.asyncio
async def test_abuse_policy_phone_daily_cap_db(session: AsyncSession, enable_rate_limit):
    """AbusePolicy blocks when phone reaches daily call cap (DB check)."""
    # Create 5 calls (hitting the cap) for the phone
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    for i in range(5):
        call = Call(
            phone="+79991234567",
            status=CallStatus.CREATED,
            created_at=today_start + timedelta(hours=i),
        )
        session.add(call)
    await session.commit()

    redis_mock = AsyncMock()
    redis_mock.eval = AsyncMock(side_effect=[1, 1])  # Per-key, per-IP pass
    redis_mock.exists = AsyncMock(return_value=False)  # No cooldown

    policy = AbusePolicy(redis_mock, session)

    # 6th call should hit the cap (5 per day)
    with pytest.raises(RateLimitError) as exc_info:
        await policy.check_call_create(api_key="test-key", phone="+79991234567", ip="127.0.0.1")

    assert "maximum calls per day" in str(exc_info.value)


@pytest.mark.asyncio
async def test_abuse_policy_max_concurrent_db(session: AsyncSession, enable_rate_limit):
    """AbusePolicy blocks when max concurrent call limit is reached."""
    # Create many active calls with different phones so we don't trigger daily cap
    for i in range(50):  # Hitting max_concurrent_calls limit
        call = Call(phone=f"+799912345{i:02d}", status=CallStatus.IN_PROGRESS)
        session.add(call)
    await session.commit()

    redis_mock = AsyncMock()
    redis_mock.eval = AsyncMock(side_effect=[1, 1])  # Per-key, per-IP pass
    redis_mock.exists = AsyncMock(return_value=False)  # No cooldown

    policy = AbusePolicy(redis_mock, session)

    # 51st call should hit the concurrent limit (50 max)
    # Use a unique phone not seen before to avoid hitting the daily cap
    with pytest.raises(RateLimitError) as exc_info:
        await policy.check_call_create(api_key="test-key", phone="+78005553333", ip="127.0.0.1")

    assert "maximum concurrent calls capacity" in str(exc_info.value)


# ── AbusePolicy Steering Tests ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_abuse_policy_steer_flood(session: AsyncSession, enable_rate_limit):
    """AbusePolicy blocks steering when per-call per-minute limit is exceeded."""
    redis_mock = AsyncMock()
    redis_mock.eval = AsyncMock(return_value=21)  # Exceeds limit=20

    policy = AbusePolicy(redis_mock, session)

    with pytest.raises(RateLimitError) as exc_info:
        await policy.check_steer(api_key="test-key", call_id="550e8400-e29b-41d4-a716-446655440000")

    assert "steering per call per minute" in str(exc_info.value)


# ── AbusePolicy Transfer Tests ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_abuse_policy_transfer_cooldown(session: AsyncSession, enable_rate_limit):
    """AbusePolicy blocks transfer when phone cooldown TTL exists."""
    redis_mock = AsyncMock()
    redis_mock.exists = AsyncMock(return_value=True)  # Cooldown exists

    policy = AbusePolicy(redis_mock, session)

    with pytest.raises(RateLimitError) as exc_info:
        await policy.check_transfer(call_id="550e8400-e29b-41d4-a716-446655440000", phone="+79991234567")

    assert "cannot be transferred again so soon" in str(exc_info.value)


@pytest.mark.asyncio
async def test_abuse_policy_transfer_cooldown_sets_ttl(session: AsyncSession, enable_rate_limit):
    """AbusePolicy sets transfer cooldown TTL after passing checks."""
    redis_mock = AsyncMock()
    redis_mock.exists = AsyncMock(return_value=False)  # No cooldown
    redis_mock.setex = AsyncMock()

    policy = AbusePolicy(redis_mock, session)

    await policy.check_transfer(call_id="550e8400-e29b-41d4-a716-446655440000", phone="+79991234567")

    # setex should have been called to set transfer cooldown
    redis_mock.setex.assert_called_once()
    call_args = redis_mock.setex.call_args
    assert "rl:transfer_cd:" in call_args[0][0]


# ── Global Settings Tests ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rate_limit_disabled_bypasses_all(session: AsyncSession, enable_rate_limit):
    """When rate_limit_enabled=False, all checks pass without Redis."""
    with patch("app.core.rate_limit.settings.rate_limit_enabled", False):
        redis_mock = AsyncMock()
        policy = AbusePolicy(redis_mock, session)

        # Should pass without any Redis calls
        await policy.check_call_create(api_key="test-key", phone="+79991234567", ip="127.0.0.1")
        redis_mock.eval.assert_not_called()


# ── Bot Error Message Tests ───────────────────────────────────────────────────

def test_bot_call_shows_friendly_429_message():
    """Bot shows friendly message for 429 errors in /call."""
    # This is a code inspection test — verify that 429 branch exists in commands.py
    from bot.handlers import commands
    import inspect
    source = inspect.getsource(commands.cmd_call)
    assert "429" in source
    assert "Слишком много запросов" in source


def test_bot_steer_callback_shows_429_alert():
    """Bot shows friendly message for 429 errors in steer callback."""
    from bot.handlers import callbacks
    import inspect
    source = inspect.getsource(callbacks.cb_steer)
    assert "429" in source
    assert "Слишком много запросов" in source


def test_bot_transfer_callback_shows_429_alert():
    """Bot shows friendly message for 429 errors in transfer callback."""
    from bot.handlers import callbacks
    import inspect
    source = inspect.getsource(callbacks.cb_transfer)
    assert "429" in source
    assert "Слишком много запросов" in source

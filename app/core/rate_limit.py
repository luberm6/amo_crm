"""
Rate limiting and abuse prevention layer.

Architecture:
  RateLimiter — low-level Redis fixed-window counter operations (fail-open)
  AbusePolicy — high-level semantic checks (all limits in one place)

All Redis-based checks are fail-open: if Redis is unavailable, limits are skipped
with a warning logged. DB-based checks (phone daily cap, concurrent calls) always run.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from redis.asyncio import Redis
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import RateLimitError
from app.core.logging import get_logger
from app.models.call import Call, CallStatus, TERMINAL_STATUSES

log = get_logger(__name__)


class RateLimiter:
    """
    Low-level Redis fixed-window counter operations.
    Atomic INCR + EXPIRE via Lua script.
    """

    # Lua script for atomic increment with automatic expiration
    # Returns the new count; sets expiration only on first increment in the window
    _LUA_INCR_EXPIRE = """
    local key = KEYS[1]
    local limit = tonumber(ARGV[1])
    local window = tonumber(ARGV[2])

    local count = redis.call('INCR', key)
    if count == 1 then
        redis.call('EXPIRE', key, window)
    end
    return count
    """

    def __init__(self, redis: Redis | None) -> None:
        self._redis = redis

    async def check_fixed_window(
        self, key: str, limit: int, window_seconds: int, label: str
    ) -> None:
        """
        Check and increment a fixed-window counter.

        Args:
            key: Redis key for the counter (e.g., "rl:calls:key:api-key-123")
            limit: Max allowed requests in the window
            window_seconds: Window duration in seconds
            label: Human-readable label for error messages

        Raises:
            RateLimitError: if count exceeds limit

        Fails open (no raise) if Redis is unavailable.
        """
        if self._redis is None:
            log.debug("redis_unavailable.skipping_rate_limit_check", label=label)
            return

        try:
            count = await self._redis.eval(
                self._LUA_INCR_EXPIRE, 1, key, limit, window_seconds
            )
            if count > limit:
                log.warning(
                    "rate_limit_exceeded",
                    label=label,
                    key=key,
                    count=count,
                    limit=limit,
                )
                raise RateLimitError(
                    f"Rate limit exceeded: {label}",
                    detail={"label": label, "limit": limit},
                )
        except RateLimitError:
            raise
        except Exception as exc:
            log.warning(
                "redis_error_during_rate_limit_check",
                label=label,
                error=str(exc),
            )
            # Fail open: log and continue


class AbusePolicy:
    """
    High-level semantic rate limit and abuse prevention checks.
    Single policy class — all rules declared here, no scattered checks.

    Combines Redis-based counters (fail-open) with DB-based queries (always enforced).
    """

    def __init__(self, redis: Redis | None, session: AsyncSession) -> None:
        self._limiter = RateLimiter(redis)
        self._session = session

    async def check_call_create(
        self, api_key: str, phone: str, ip: str
    ) -> None:
        """
        Pre-flight checks before creating a new call.

        1. Per-API-key per-minute limit (Redis)
        2. Per-IP per-minute limit (Redis)
        3. Phone repeat cooldown (Redis TTL)
        4. Phone daily cap (DB query)
        5. Max concurrent calls (DB query)

        After all checks pass, sets phone cooldown TTL.

        Args:
            api_key: X-API-Key header value (or empty string if not provided)
            phone: Normalized phone number
            ip: Client IP address

        Raises:
            RateLimitError: if any check fails
        """
        if not settings.rate_limit_enabled:
            return

        # 1. Per-API-key per-minute
        if api_key:
            await self._limiter.check_fixed_window(
                key=f"rl:calls:key:{api_key}",
                limit=settings.rate_limit_calls_per_minute,
                window_seconds=60,
                label=f"calls per API key per minute ({api_key[:8]}...)",
            )

        # 2. Per-IP per-minute
        await self._limiter.check_fixed_window(
            key=f"rl:calls:ip:{ip}",
            limit=settings.rate_limit_global_per_ip_per_minute,
            window_seconds=60,
            label=f"calls per IP per minute ({ip})",
        )

        # 3. Phone repeat cooldown check (Redis TTL)
        if self._limiter._redis is not None:
            cooldown_key = f"rl:phone_cd:{phone}"
            try:
                exists = await self._limiter._redis.exists(cooldown_key)
                if exists:
                    raise RateLimitError(
                        f"Phone {phone} cannot be called again so soon",
                        detail={"phone": phone},
                    )
            except RateLimitError:
                raise
            except Exception as exc:
                log.warning(
                    "redis_error_checking_phone_cooldown",
                    phone=phone,
                    error=str(exc),
                )

        # 4. Phone daily cap (DB query)
        await self._check_phone_daily_cap(phone)

        # 5. Max concurrent calls (DB query)
        await self._check_max_concurrent_calls()

        # All checks passed — set phone cooldown TTL
        if self._limiter._redis is not None:
            cooldown_key = f"rl:phone_cd:{phone}"
            try:
                await self._limiter._redis.setex(
                    cooldown_key,
                    settings.rate_limit_phone_repeat_cooldown_seconds,
                    "1",
                )
            except Exception as exc:
                log.warning(
                    "redis_error_setting_phone_cooldown",
                    phone=phone,
                    error=str(exc),
                )

    async def check_steer(self, api_key: str, call_id: str) -> None:
        """
        Rate limit steering instructions.

        Per-call per-minute steer limit (Redis).

        Args:
            api_key: X-API-Key header value (for audit)
            call_id: UUID of the call

        Raises:
            RateLimitError: if limit exceeded
        """
        if not settings.rate_limit_enabled:
            return

        await self._limiter.check_fixed_window(
            key=f"rl:steer:{call_id}",
            limit=settings.rate_limit_steer_per_call_per_minute,
            window_seconds=60,
            label=f"steering per call per minute ({call_id})",
        )

    async def check_transfer(self, call_id: str, phone: str) -> None:
        """
        Rate limit transfer attempts.

        Transfer cooldown per phone (Redis TTL).

        Args:
            call_id: UUID of the call
            phone: Normalized phone number

        Raises:
            RateLimitError: if cooldown still active
        """
        if not settings.rate_limit_enabled:
            return

        # Transfer cooldown per phone (Redis TTL)
        if self._limiter._redis is not None:
            transfer_cooldown_key = f"rl:transfer_cd:{phone}"
            try:
                exists = await self._limiter._redis.exists(transfer_cooldown_key)
                if exists:
                    raise RateLimitError(
                        f"Phone {phone} cannot be transferred again so soon",
                        detail={"phone": phone},
                    )
            except RateLimitError:
                raise
            except Exception as exc:
                log.warning(
                    "redis_error_checking_transfer_cooldown",
                    phone=phone,
                    error=str(exc),
                )

        # After pass: set transfer cooldown TTL
        if self._limiter._redis is not None:
            transfer_cooldown_key = f"rl:transfer_cd:{phone}"
            try:
                await self._limiter._redis.setex(
                    transfer_cooldown_key,
                    settings.rate_limit_transfer_cooldown_seconds,
                    "1",
                )
            except Exception as exc:
                log.warning(
                    "redis_error_setting_transfer_cooldown",
                    phone=phone,
                    error=str(exc),
                )

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _check_phone_daily_cap(self, phone: str) -> None:
        """
        Check if phone has reached the daily call limit.
        Counts calls created today (calendar day in UTC).
        """
        now_utc = datetime.now(timezone.utc)
        today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)

        stmt = select(func.count(Call.id)).where(
            and_(Call.phone == phone, Call.created_at >= today_start)
        )
        result = await self._session.execute(stmt)
        count = result.scalar() or 0

        if count >= settings.rate_limit_calls_per_phone_per_day:
            log.warning(
                "rate_limit_phone_daily_cap_exceeded",
                phone=phone,
                count=count,
                limit=settings.rate_limit_calls_per_phone_per_day,
            )
            raise RateLimitError(
                f"Phone {phone} has reached maximum calls per day",
                detail={"phone": phone, "limit": settings.rate_limit_calls_per_phone_per_day},
            )

    async def _check_max_concurrent_calls(self) -> None:
        """Check if system-wide concurrent call limit is reached."""
        stmt = select(func.count(Call.id)).where(
            ~Call.status.in_(TERMINAL_STATUSES)
        )
        result = await self._session.execute(stmt)
        active_count = result.scalar() or 0

        if active_count >= settings.rate_limit_max_concurrent_calls:
            log.warning(
                "rate_limit_max_concurrent_calls_exceeded",
                active_count=active_count,
                limit=settings.rate_limit_max_concurrent_calls,
            )
            raise RateLimitError(
                "System is at maximum concurrent calls capacity",
                detail={"limit": settings.rate_limit_max_concurrent_calls},
            )

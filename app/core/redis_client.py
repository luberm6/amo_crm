"""
Async Redis singleton for application-wide use.
Lifecycle managed by FastAPI lifespan hooks to ensure proper initialization and cleanup.

Initialize with init_redis() during app startup.
Clean up with close_redis() during app shutdown.
Access via get_redis() — returns None if not initialized or unavailable.
"""
from __future__ import annotations

from redis.asyncio import Redis
from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

# Global Redis instance — None until init_redis() is called
_redis: Redis | None = None


async def init_redis() -> None:
    """
    Initialize the global Redis connection.
    Called during app startup via lifespan context manager.
    """
    global _redis
    try:
        _redis = await Redis.from_url(settings.redis_url, decode_responses=True)
        # Test connectivity
        await _redis.ping()
        log.info("redis_initialized", url=settings.redis_url.split("://")[0] + "://...")
    except Exception as exc:
        log.warning("redis_initialization_failed", error=str(exc))
        _redis = None


async def close_redis() -> None:
    """
    Gracefully close the Redis connection.
    Called during app shutdown via lifespan context manager.
    """
    global _redis
    if _redis is not None:
        try:
            await _redis.close()
            log.info("redis_closed")
        except Exception as exc:
            log.warning("redis_close_error", error=str(exc))
        finally:
            _redis = None


def get_redis() -> Redis | None:
    """
    Return the global Redis instance.
    Returns None if Redis is not initialized or unavailable.
    Callers should handle None gracefully (fail-open pattern).
    """
    return _redis

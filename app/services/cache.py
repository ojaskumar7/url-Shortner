import logging

from redis.asyncio import Redis

logger = logging.getLogger(__name__)


def cache_key(slug: str) -> str:
    """Return the Redis key for a slug."""
    return f"link:{slug}"


async def get_cached_target(redis: Redis, slug: str) -> str | None:
    """Read a cached target URL, returning None on a miss or Redis failure."""
    try:
        return await redis.get(cache_key(slug))
    except Exception:
        logger.warning(
            "Redis GET failed for %s; falling back to PostgreSQL",
            cache_key(slug),
            exc_info=True,
        )
        return None


async def set_cached_target(
    redis: Redis,
    slug: str,
    target_url: str,
    ttl_seconds: int,
) -> None:
    """Cache a target URL for at most its remaining lifetime."""
    try:
        await redis.set(cache_key(slug), target_url, ex=ttl_seconds)
    except Exception:
        logger.warning(
            "Redis SET failed for %s; continuing without cache",
            cache_key(slug),
            exc_info=True,
        )


async def delete_cached_target(redis: Redis, slug: str) -> None:
    """Invalidate a cached target URL, ignoring Redis failures."""
    try:
        await redis.delete(cache_key(slug))
    except Exception:
        logger.warning(
            "Redis DEL failed for %s",
            cache_key(slug),
            exc_info=True,
        )

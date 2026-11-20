from fastapi import Request
from redis.asyncio import Redis

from app.core.config import settings


def create_redis_client() -> Redis:
    """Create an async Redis client from configuration."""
    return Redis.from_url(settings.REDIS_URL, decode_responses=True)


def get_redis(request: Request) -> Redis:
    """Return the Redis client stored on the application state."""
    return request.app.state.redis

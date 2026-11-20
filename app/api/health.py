import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.redis import get_redis

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health")
async def health(
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> JSONResponse:
    """Report PostgreSQL and Redis connectivity independently."""
    statuses = {"postgres": "ok", "redis": "ok"}

    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        logger.warning("PostgreSQL health check failed", exc_info=True)
        statuses["postgres"] = "error"

    try:
        await redis.ping()
    except Exception:
        logger.warning("Redis health check failed", exc_info=True)
        statuses["redis"] = "error"

    status_code = 200 if all(value == "ok" for value in statuses.values()) else 503
    return JSONResponse(status_code=status_code, content=statuses)

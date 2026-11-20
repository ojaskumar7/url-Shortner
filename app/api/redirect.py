from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.redis import get_redis
from app.services import cache, link_service

router = APIRouter()


@router.get("/{slug}", response_class=RedirectResponse)
async def redirect_to_target(
    slug: str,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> RedirectResponse:
    """Redirect a short slug, using Redis as a cache-aside layer."""
    cached_target = await cache.get_cached_target(redis, slug)
    if cached_target is not None:
        return RedirectResponse(url=cached_target, status_code=302)

    link = await link_service.get_link(session, slug)
    if link is None:
        raise HTTPException(status_code=404, detail="Link not found")
    if link_service.is_expired(link):
        raise HTTPException(status_code=410, detail="Link has expired")

    await cache.set_cached_target(
        redis,
        slug,
        link.target_url,
        link_service.cache_ttl_seconds(link),
    )
    return RedirectResponse(url=link.target_url, status_code=302)

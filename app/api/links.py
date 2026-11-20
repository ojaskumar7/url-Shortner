from fastapi import APIRouter, Depends, HTTPException, Response, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.redis import get_redis
from app.schemas.link import LinkCreate, LinkResponse
from app.services import cache, link_service

router = APIRouter(prefix="/api/links", tags=["links"])


@router.post("", response_model=LinkResponse, status_code=status.HTTP_201_CREATED)
async def create_link(
    payload: LinkCreate,
    session: AsyncSession = Depends(get_session),
) -> LinkResponse:
    """Create a short link from a long URL."""
    try:
        link = await link_service.create_link(session, payload)
    except link_service.AliasConflictError:
        raise HTTPException(status_code=409, detail="Alias already in use")
    except link_service.SlugGenerationError:
        raise HTTPException(status_code=500, detail="Could not generate a unique slug")
    return link_service.link_to_response(link)


@router.get("/{slug}", response_model=LinkResponse)
async def get_link_info(
    slug: str,
    session: AsyncSession = Depends(get_session),
) -> LinkResponse:
    """Return metadata for a short link."""
    link = await link_service.get_link(session, slug)
    if link is None:
        raise HTTPException(status_code=404, detail="Link not found")
    if link_service.is_expired(link):
        raise HTTPException(status_code=410, detail="Link has expired")
    return link_service.link_to_response(link)


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_link(
    slug: str,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> Response:
    """Delete a short link and invalidate its cache entry."""
    deleted = await link_service.delete_link(session, slug)
    if not deleted:
        raise HTTPException(status_code=404, detail="Link not found")
    await cache.delete_cached_target(redis, slug)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

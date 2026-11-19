import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.link import Link
from app.schemas.link import LinkCreate, LinkResponse
from app.services.base62 import generate_slug

logger = logging.getLogger(__name__)

MAX_SLUG_ATTEMPTS = 5


class AliasConflictError(Exception):
    """Raised when a requested custom alias already exists."""


class SlugGenerationError(Exception):
    """Raised when random slug generation cannot find a free slug."""


def utc_now() -> datetime:
    """Return the current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def is_expired(link: Link, now: datetime | None = None) -> bool:
    """Return whether a link has an expiration time at or before now."""
    if link.expires_at is None:
        return False
    return link.expires_at <= (now or utc_now())


def cache_ttl_seconds(link: Link) -> int:
    """Return the Redis TTL for a valid link, at least one second."""
    if link.expires_at is None:
        return settings.DEFAULT_CACHE_TTL_SECONDS
    remaining = int((link.expires_at - utc_now()).total_seconds())
    return max(1, remaining)


def link_to_response(link: Link) -> LinkResponse:
    """Build the API response shape from an ORM link."""
    return LinkResponse(
        slug=link.slug,
        short_url=f"{settings.BASE_URL.rstrip('/')}/{link.slug}",
        target_url=link.target_url,
        created_at=link.created_at,
        expires_at=link.expires_at,
    )


async def create_link(session: AsyncSession, payload: LinkCreate) -> Link:
    """Create a link, retrying random slugs on unique-constraint collisions."""
    expires_at = None
    if payload.expires_in_seconds is not None:
        expires_at = utc_now() + timedelta(seconds=payload.expires_in_seconds)

    if payload.custom_alias is not None:
        link = Link(
            slug=payload.custom_alias,
            target_url=payload.url,
            expires_at=expires_at,
        )
        session.add(link)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise AliasConflictError from exc
        await session.refresh(link)
        return link

    for _ in range(MAX_SLUG_ATTEMPTS):
        link = Link(
            slug=generate_slug(settings.SLUG_LENGTH),
            target_url=payload.url,
            expires_at=expires_at,
        )
        session.add(link)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            logger.warning("Slug collision detected; retrying with a new slug")
            continue
        await session.refresh(link)
        return link

    raise SlugGenerationError


async def get_link(session: AsyncSession, slug: str) -> Link | None:
    """Look up a link by slug in PostgreSQL."""
    result = await session.execute(select(Link).where(Link.slug == slug))
    return result.scalar_one_or_none()


async def delete_link(session: AsyncSession, slug: str) -> bool:
    """Delete a link by slug. Return whether a row was deleted."""
    link = await get_link(session, slug)
    if link is None:
        return False
    await session.delete(link)
    await session.commit()
    return True

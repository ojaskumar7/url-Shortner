# URL Shortener Implementation Plan

> **Progress:** Task 0 and Task 1 are complete as of 2026-11-20. See `docs/progress.md` for verified results, commits, and environment notes. The checkboxes below remain the original execution checklist.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a small FastAPI URL shortener backed by PostgreSQL and Redis, with random Base62 slugs, custom aliases, expiry, cache-aside redirects, and consistent errors.

**Architecture:** FastAPI routes delegate to small service modules. PostgreSQL is the source of truth through async SQLAlchemy. Redis is an optional cache-aside layer for `GET /{slug}`; Redis failures fall back to PostgreSQL. Startup creates tables with `Base.metadata.create_all` and initializes the Redis client.

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, Pydantic v2, pydantic-settings, SQLAlchemy 2.0 async, asyncpg, redis-py async, PostgreSQL 16, Redis 7, Docker Compose.

## Global Constraints

- Python 3.12.
- `requirements.txt` contains exactly: `fastapi`, `uvicorn[standard]`, `sqlalchemy[asyncio]`, `asyncpg`, `redis`, `pydantic-settings`.
- All configuration comes from one `Settings` class in `app/core/config.py`, loaded from `.env`.
- PostgreSQL is the source of truth; Redis is cache-aside only.
- Redirects use **302**, never 301.
- All datetimes are timezone-aware UTC.
- Do not add auth, users, rate limiting, click analytics, Alembic, negative caching, automated tests, or an app Dockerfile.
- Automated tests are explicitly out of scope. Verification is manual with `curl`, `psql`, and `redis-cli`.
- Routers stay thin. Business logic lives in `app/services/`.
- Register `/health` and `/api/*` before the `/{slug}` catch-all.
- Redis failures must not break redirects or deletes; log a warning and continue.
- Commit after each milestone. Stop after each milestone and summarize.

## File Map

| File | Responsibility |
|---|---|
| `app/main.py` | App factory, lifespan, router registration in safe order |
| `app/core/config.py` | `Settings` and singleton `settings` |
| `app/core/database.py` | Async engine, session factory, `Base`, `get_session` |
| `app/core/redis.py` | Redis client factory and `get_redis` dependency |
| `app/models/link.py` | `Link` SQLAlchemy model |
| `app/schemas/link.py` | Request validation and response schema |
| `app/services/base62.py` | Pure Base62 slug generator |
| `app/services/link_service.py` | Create/get/delete logic, collision retry, expiry helpers |
| `app/services/cache.py` | Redis get/set/delete helpers with graceful failure |
| `app/api/links.py` | `POST /api/links`, `GET /api/links/{slug}`, `DELETE /api/links/{slug}` |
| `app/api/redirect.py` | `GET /{slug}` catch-all redirect |
| `app/api/health.py` | `GET /health` |
| `docker-compose.yml` | PostgreSQL 16 and Redis 7 only |
| `requirements.txt` | Python dependencies |
| `.env.example` | Required environment variables |
| `.gitignore` | Local env, virtualenv, and bytecode ignores |
| `README.md` | Setup, curl examples, design decisions |
| `SPEC.md` | Approved specification |

---

## Task 0: Write and commit the plan

**Files:**
- Create: `docs/plan.md`
- Create: `.gitignore`

- [ ] **Step 1: Create `.gitignore`**

```gitignore
.venv/
__pycache__/
*.py[cod]
.env
.pytest_cache/
.DS_Store
```

- [ ] **Step 2: Commit only the plan and ignore file**

```bash
git add docs/plan.md .gitignore
git commit -m "chore: add implementation plan"
```

Expected: commit succeeds; `.venv/` is not staged.

---

## Task 1: Scaffold, configuration, database, model, and PostgreSQL health

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `docker-compose.yml`
- Create: `SPEC.md`
- Create: `app/__init__.py`
- Create: `app/core/__init__.py`
- Create: `app/models/__init__.py`
- Create: `app/schemas/__init__.py`
- Create: `app/services/__init__.py`
- Create: `app/api/__init__.py`
- Create: `app/core/config.py`
- Create: `app/core/database.py`
- Create: `app/models/link.py`
- Create: `app/api/health.py`
- Create: `app/main.py`

**Interfaces:**
- Produces: `settings` with `DATABASE_URL`, `REDIS_URL`, `BASE_URL`, `SLUG_LENGTH`, `DEFAULT_CACHE_TTL_SECONDS`.
- Produces: `Base`, `engine`, `SessionLocal`, `get_session`.
- Produces: `Link` ORM model with `id`, `slug`, `target_url`, `created_at`, `expires_at`.
- Produces: `GET /health` returning PostgreSQL status.

- [ ] **Step 1: Create dependency and environment files**

`requirements.txt`:

```text
fastapi
uvicorn[standard]
sqlalchemy[asyncio]
asyncpg
redis
pydantic-settings
```

`.env.example`:

```dotenv
DATABASE_URL=postgresql+asyncpg://shortener:shortener@localhost:5432/shortener
REDIS_URL=redis://localhost:6379/0
BASE_URL=http://localhost:8000
SLUG_LENGTH=7
DEFAULT_CACHE_TTL_SECONDS=86400
```

`docker-compose.yml`:

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: shortener
      POSTGRES_PASSWORD: shortener
      POSTGRES_DB: shortener
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

  redis:
    image: redis:7
    ports:
      - "6379:6379"

volumes:
  postgres_data:
```

`SPEC.md`: save the approved specification from the task prompt verbatim.

- [ ] **Step 2: Create empty package markers**

Create empty `__init__.py` files in `app/`, `app/core/`, `app/models/`, `app/schemas/`, `app/services/`, and `app/api/`.

- [ ] **Step 3: Implement `app/core/config.py`**

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DATABASE_URL: str = "postgresql+asyncpg://shortener:shortener@localhost:5432/shortener"
    REDIS_URL: str = "redis://localhost:6379/0"
    BASE_URL: str = "http://localhost:8000"
    SLUG_LENGTH: int = 7
    DEFAULT_CACHE_TTL_SECONDS: int = 86400


settings = Settings()
```

- [ ] **Step 4: Implement `app/core/database.py`**

```python
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
SessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield one async database session per request."""
    async with SessionLocal() as session:
        yield session
```

- [ ] **Step 5: Implement `app/models/link.py`**

```python
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Link(Base):
    """A shortened URL and its optional expiration time."""

    __tablename__ = "links"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(
        String(30),
        unique=True,
        index=True,
        nullable=False,
    )
    target_url: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
```

- [ ] **Step 6: Implement PostgreSQL-only `app/api/health.py`**

```python
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session

router = APIRouter()


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    """Report whether PostgreSQL is reachable."""
    await session.execute(text("SELECT 1"))
    return {"postgres": "ok"}
```

- [ ] **Step 7: Implement `app/main.py` with lifespan and route order**

```python
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health
from app.core.database import Base, engine
from app.models import link  # noqa: F401  (registers the Link model)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create tables and release database resources on shutdown."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables ready")
    try:
        yield
    finally:
        await engine.dispose()


app = FastAPI(title="URL Shortener", lifespan=lifespan)

# Register fixed paths before the /{slug} catch-all added later.
app.include_router(health.router)
```

- [ ] **Step 8: Install dependencies and start infrastructure**

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
docker compose up -d
```

Expected: PostgreSQL and Redis containers are running.

- [ ] **Step 9: Run the app and verify PostgreSQL health**

```bash
uvicorn app.main:app --reload
```

In another terminal:

```bash
curl -i localhost:8000/health
```

Expected: HTTP 200 and `{"postgres":"ok"}`.

- [ ] **Step 10: Verify the schema**

```bash
docker compose exec postgres psql -U shortener -d shortener -c '\d links'
```

Expected: `links` table with a unique index on `slug`.

- [ ] **Step 11: Commit milestone 1**

```bash
git add requirements.txt .env.example docker-compose.yml SPEC.md app
git commit -m "feat: scaffold app config database and health"
```

---

## Task 2: Base62 slug generation, validation, and link creation

**Files:**
- Create: `app/services/base62.py`
- Create: `app/schemas/link.py`
- Create: `app/services/link_service.py`
- Create: `app/api/links.py`
- Modify: `app/main.py`

**Interfaces:**
- Consumes: `Link`, `get_session`, `settings`.
- Produces: `generate_slug(length: int = 7) -> str`.
- Produces: `LinkCreate`, `LinkResponse`.
- Produces: `create_link(session, payload) -> Link`, `get_link(session, slug) -> Link | None`, `is_expired(link) -> bool`, `link_to_response(link) -> LinkResponse`, `AliasConflictError`, `SlugGenerationError`.
- Produces: `POST /api/links` returning 201.

- [ ] **Step 1: Implement `app/services/base62.py`**

```python
import secrets
import string

ALPHABET = string.digits + string.ascii_uppercase + string.ascii_lowercase


def generate_slug(length: int = 7) -> str:
    """Return a random Base62 slug of the requested length."""
    return "".join(secrets.choice(ALPHABET) for _ in range(length))
```

- [ ] **Step 2: Implement `app/schemas/link.py`**

```python
import re
from datetime import datetime
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

RESERVED_ALIASES = {
    "api",
    "docs",
    "redoc",
    "openapi.json",
    "health",
    "admin",
    "static",
}
ALIAS_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class LinkCreate(BaseModel):
    """Validated payload for creating a short link."""

    url: str = Field(min_length=1, max_length=2048)
    custom_alias: str | None = Field(default=None, min_length=3, max_length=30)
    expires_in_seconds: int | None = Field(default=None, ge=60, le=31_536_000)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("URL must be a valid http or https URL")
        return value

    @field_validator("custom_alias")
    @classmethod
    def validate_custom_alias(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not ALIAS_PATTERN.fullmatch(value):
            raise ValueError(
                "custom_alias may contain only letters, numbers, hyphens, and underscores"
            )
        if value.lower() in RESERVED_ALIASES:
            raise ValueError("custom_alias is reserved")
        return value


class LinkResponse(BaseModel):
    """Public representation of a short link."""

    model_config = ConfigDict(from_attributes=True)

    slug: str
    short_url: str
    target_url: str
    created_at: datetime
    expires_at: datetime | None
```

- [ ] **Step 3: Implement `app/services/link_service.py`**

```python
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
```

- [ ] **Step 4: Implement `app/api/links.py` with the create route**

```python
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.schemas.link import LinkCreate, LinkResponse
from app.services import link_service

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
```

- [ ] **Step 5: Register the links router in `app/main.py`**

Add:

```python
from app.api import health, links
```

and after the health router:

```python
app.include_router(links.router)
```

- [ ] **Step 6: Verify creation and validation**

```bash
curl -i -X POST localhost:8000/api/links \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com"}'
```

Expected: 201, a 7-character Base62 slug, and `short_url` beginning with `http://localhost:8000/`.

```bash
curl -i -X POST localhost:8000/api/links \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com","custom_alias":"my-link"}'
```

Expected: 201. Repeat the same request: expected 409 `"Alias already in use"`.

```bash
curl -i -X POST localhost:8000/api/links \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com","custom_alias":"api"}'
```

Expected: 422 reserved alias.

```bash
curl -i -X POST localhost:8000/api/links \
  -H "Content-Type: application/json" \
  -d '{"url":"ftp://example.com"}'
```

Expected: 422 invalid URL.

- [ ] **Step 7: Commit milestone 2**

```bash
git add app/services/base62.py app/schemas/link.py app/services/link_service.py app/api/links.py app/main.py
git commit -m "feat: add link creation endpoint"
```

---

## Task 3: Redirect directly from PostgreSQL

**Files:**
- Create: `app/api/redirect.py`
- Modify: `app/main.py`

**Interfaces:**
- Consumes: `get_link`, `is_expired`, `get_session`.
- Produces: `GET /{slug}` returning 302, 404, or 410.

- [ ] **Step 1: Implement `app/api/redirect.py`**

```python
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.services import link_service

router = APIRouter()


@router.get("/{slug}", response_class=RedirectResponse)
async def redirect_to_target(
    slug: str,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Redirect a short slug to its target URL."""
    link = await link_service.get_link(session, slug)
    if link is None:
        raise HTTPException(status_code=404, detail="Link not found")
    if link_service.is_expired(link):
        raise HTTPException(status_code=410, detail="Link has expired")
    return RedirectResponse(url=link.target_url, status_code=302)
```

- [ ] **Step 2: Register the redirect router last in `app/main.py`**

Add `redirect` to the import:

```python
from app.api import health, links, redirect
```

Then after the links router:

```python
# The catch-all must be registered after all fixed paths.
app.include_router(redirect.router)
```

- [ ] **Step 3: Verify redirect behavior**

Create a link, copy its slug, then:

```bash
curl -i localhost:8000/<slug>
```

Expected: HTTP 302 with `Location: <target_url>`.

```bash
curl -i localhost:8000/does-not-exist
```

Expected: HTTP 404 `{"detail":"Link not found"}`.

Create a link with `"expires_in_seconds":60`, wait 60 seconds, then request it.

Expected: HTTP 410 `{"detail":"Link has expired"}`.

- [ ] **Step 4: Commit milestone 3**

```bash
git add app/api/redirect.py app/main.py
git commit -m "feat: add redirect endpoint"
```

---

## Task 4: Link info and deletion

**Files:**
- Modify: `app/services/link_service.py`
- Modify: `app/api/links.py`

**Interfaces:**
- Produces: `delete_link(session, slug) -> bool`.
- Produces: `GET /api/links/{slug}` and `DELETE /api/links/{slug}`.

- [ ] **Step 1: Add `delete_link` to `app/services/link_service.py`**

```python
async def delete_link(session: AsyncSession, slug: str) -> bool:
    """Delete a link by slug. Return whether a row was deleted."""
    link = await get_link(session, slug)
    if link is None:
        return False
    await session.delete(link)
    await session.commit()
    return True
```

- [ ] **Step 2: Add info and delete routes to `app/api/links.py`**

```python
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
) -> Response:
    """Delete a short link."""
    deleted = await link_service.delete_link(session, slug)
    if not deleted:
        raise HTTPException(status_code=404, detail="Link not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

Add `Response` to the FastAPI imports in that file:

```python
from fastapi import APIRouter, Depends, HTTPException, Response, status
```

- [ ] **Step 3: Verify info and deletion**

```bash
curl -i localhost:8000/api/links/<slug>
```

Expected: 200 with the same shape as the create response.

```bash
curl -i localhost:8000/api/links/does-not-exist
```

Expected: 404.

```bash
curl -i -X DELETE localhost:8000/api/links/<slug>
```

Expected: 204 with an empty body. A subsequent redirect or info request returns 404.

- [ ] **Step 4: Commit milestone 4**

```bash
git add app/services/link_service.py app/api/links.py
git commit -m "feat: add link info and delete endpoints"
```

---

## Task 5: Redis cache-aside, invalidation, and full health check

**Files:**
- Create: `app/services/cache.py`
- Modify: `app/core/redis.py`
- Modify: `app/main.py`
- Modify: `app/api/redirect.py`
- Modify: `app/api/links.py`
- Modify: `app/api/health.py`

**Interfaces:**
- Consumes: `settings.REDIS_URL`, `settings.DEFAULT_CACHE_TTL_SECONDS`, `cache_ttl_seconds`.
- Produces: `create_redis_client() -> Redis`, `get_redis(request) -> Redis`.
- Produces: `get_cached_target`, `set_cached_target`, `delete_cached_target`.
- Produces: cache-aside redirect and full `/health`.

- [ ] **Step 1: Implement `app/core/redis.py`**

```python
from fastapi import Request
from redis.asyncio import Redis

from app.core.config import settings


def create_redis_client() -> Redis:
    """Create an async Redis client from configuration."""
    return Redis.from_url(settings.REDIS_URL, decode_responses=True)


def get_redis(request: Request) -> Redis:
    """Return the Redis client stored on the application state."""
    return request.app.state.redis
```

- [ ] **Step 2: Implement `app/services/cache.py`**

```python
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
        logger.warning("Redis GET failed for %s; falling back to PostgreSQL", cache_key(slug), exc_info=True)
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
        logger.warning("Redis SET failed for %s; continuing without cache", cache_key(slug), exc_info=True)


async def delete_cached_target(redis: Redis, slug: str) -> None:
    """Invalidate a cached target URL, ignoring Redis failures."""
    try:
        await redis.delete(cache_key(slug))
    except Exception:
        logger.warning("Redis DEL failed for %s", cache_key(slug), exc_info=True)
```

- [ ] **Step 3: Initialize and close Redis in the `app/main.py` lifespan**

```python
from app.core.redis import create_redis_client

# Inside lifespan, after table creation:
app.state.redis = create_redis_client()

# In the finally block:
await app.state.redis.aclose()
await engine.dispose()
```

- [ ] **Step 4: Add cache-aside behavior to `app/api/redirect.py`**

```python
from redis.asyncio import Redis

from app.core.redis import get_redis
from app.services import cache, link_service


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
```

- [ ] **Step 5: Invalidate Redis on delete**

Add the Redis dependency to the delete route in `app/api/links.py`:

```python
from redis.asyncio import Redis

from app.core.redis import get_redis
from app.services import cache


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
```

- [ ] **Step 6: Replace `app/api/health.py` with the full health check**

```python
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
```

- [ ] **Step 7: Verify cache behavior**

```bash
curl -i localhost:8000/<slug>
redis-cli GET link:<slug>
```

Expected: first request 302; the Redis value is the target URL.

```bash
curl -i localhost:8000/<slug>
```

Expected: 302, served from Redis. The app log should not show a PostgreSQL lookup for this second request.

- [ ] **Step 8: Verify expiry removes the cache entry**

Create a link with `"expires_in_seconds":60`, request it immediately, wait 60 seconds, then request again.

Expected: 410, and `redis-cli GET link:<slug>` is empty.

- [ ] **Step 9: Verify delete invalidates the cache**

Create a link, request it once, then delete it.

```bash
curl -i -X DELETE localhost:8000/api/links/<slug>
curl -i localhost:8000/<slug>
redis-cli GET link:<slug>
```

Expected: 204, then 404, and an empty Redis value.

- [ ] **Step 10: Verify graceful Redis failure**

```bash
docker compose stop redis
curl -i localhost:8000/<slug>
curl -i localhost:8000/health
```

Expected: redirect still returns 302 via PostgreSQL; health returns 503 with `{"postgres":"ok","redis":"error"}`.

Restart Redis:

```bash
docker compose start redis
```

- [ ] **Step 11: Commit milestone 5**

```bash
git add app/core/redis.py app/services/cache.py app/main.py app/api/redirect.py app/api/links.py app/api/health.py
git commit -m "feat: add redis cache-aside and full health check"
```

---

## Task 6: README and final acceptance review

**Files:**
- Create: `README.md`
- Modify: `docs/plan.md` (check off completed items)

- [ ] **Step 1: Write `README.md`**

Include:

- Project description and scope.
- Requirements: Python 3.12, Docker Compose.
- Setup:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
docker compose up -d
uvicorn app.main:app --reload
```

- Curl examples for create, redirect, info, delete, and health.
- A short "Design decisions" section covering:
  - PostgreSQL as source of truth and Redis as cache-aside.
  - 302 redirects so deletion and expiry take effect immediately.
  - Unique slug index used for both collision safety and lookup speed.
  - Random slug retry with 5 attempts.
  - TTL equal to remaining link lifetime.
  - Redis failures degrade gracefully.
  - Route ordering prevents `/api/*` and `/health` from being shadowed.

- [ ] **Step 2: Run the full acceptance checklist**

Use the checklist from section 12 of `SPEC.md`. Record any failures and fix them before committing.

- [ ] **Step 3: Final review**

Confirm:

- No auth, rate limiting, analytics, Alembic, negative caching, automated tests, or app Dockerfile were added.
- No hardcoded configuration outside `Settings`.
- All I/O is async.
- All datetimes are timezone-aware UTC.
- Error responses use `{"detail": "..."}`.
- `/health` and `/api/*` are registered before `/{slug}`.
- Redis failure does not break redirects.
- `.env` is ignored and `.env.example` is committed.

- [ ] **Step 4: Commit milestone 6**

```bash
git add README.md docs/plan.md
git commit -m "docs: add README and finalize project"
```

---

## Manual Acceptance Checklist

- [ ] `docker compose up -d` starts PostgreSQL 16 and Redis 7.
- [ ] `GET /health` returns 200 with `{"postgres":"ok","redis":"ok"}`.
- [ ] `POST /api/links` with a plain URL returns 201 and a 7-character slug.
- [ ] `GET /{slug}` returns 302 with the correct `Location`.
- [ ] A second redirect is served from Redis.
- [ ] Custom alias creation works; a duplicate returns 409.
- [ ] Reserved alias returns 422.
- [ ] Invalid alias characters or length return 422.
- [ ] Invalid URL (`ftp://`, plain text, over 2048 chars) returns 422.
- [ ] Expiring links return 410 after expiry and the Redis key is gone.
- [ ] `GET /api/links/{slug}` returns link info; unknown slug returns 404.
- [ ] `DELETE /api/links/{slug}` returns 204 and invalidates the cache.
- [ ] Stopping Redis leaves redirects working; `/health` returns 503 with `"redis":"error"`.
- [ ] `\d links` in `psql` shows the unique index on `slug`.

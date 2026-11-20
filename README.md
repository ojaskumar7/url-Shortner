# URL Shortener

A small URL shortener built with FastAPI, PostgreSQL, and Redis. Clients create short links, optionally with a custom alias or an expiry, and visiting `/{slug}` redirects to the original URL.

PostgreSQL is the source of truth. Redis is an optional cache-aside layer for redirects, so the service keeps working when Redis is unavailable.

## Features

- Random Base62 slugs with database-enforced collision retry.
- Custom aliases with validation and a reserved-word list.
- Optional link expiry with timezone-aware UTC timestamps.
- `302` redirects so deletion and expiry take effect immediately.
- Redis cache-aside for the redirect path.
- Cache invalidation on delete.
- Graceful Redis failure: redirects fall back to PostgreSQL.
- `GET /health` reports PostgreSQL and Redis independently.
- Consistent JSON errors in the form `{"detail": "..."}`.

## Stack

- Python 3.12
- FastAPI and Uvicorn
- Pydantic v2 and pydantic-settings
- SQLAlchemy 2.0 async with asyncpg
- redis-py (`redis.asyncio`)
- PostgreSQL 16 and Redis 7 via Docker Compose

## Project layout

```text
app/
  main.py            # app, lifespan, router registration
  core/
    config.py        # Settings loaded from .env
    database.py      # async engine, session factory, Base
    redis.py         # Redis client and dependency
  models/
    link.py          # Link model
  schemas/
    link.py          # request validation and response schema
  services/
    base62.py        # Base62 slug generation
    link_service.py  # create/get/delete logic and expiry helpers
    cache.py         # Redis get/set/delete helpers
  api/
    health.py        # GET /health
    links.py         # POST/GET/DELETE /api/links
    redirect.py      # GET /{slug}
docker-compose.yml   # PostgreSQL and Redis only
SPEC.md              # approved specification
docs/                # implementation plan and progress log
blog/                # milestone-by-milestone technical series
```

## Requirements

- Python 3.12
- Docker and Docker Compose
- `curl` for the examples below

## Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
docker compose up -d
uvicorn app.main:app --reload
```

The API runs at `http://localhost:8000`. The app creates the `links` table on startup; there are no migrations.

If ports `5432` or `6379` are already in use, stop the conflicting services or change the Compose ports and update `DATABASE_URL` and `REDIS_URL` in `.env`.

## Configuration

All configuration comes from `.env` through `app/core/config.py`:

```dotenv
DATABASE_URL=postgresql+asyncpg://shortener:shortener@localhost:5432/shortener
REDIS_URL=redis://localhost:6379/0
BASE_URL=http://localhost:8000
SLUG_LENGTH=7
DEFAULT_CACHE_TTL_SECONDS=86400
```

`BASE_URL` is used to build the `short_url` returned by the API. `DEFAULT_CACHE_TTL_SECONDS` applies to links that never expire.

## API

### Health

```bash
curl -i localhost:8000/health
```

Healthy response:

```json
{"postgres": "ok", "redis": "ok"}
```

If either dependency fails, the endpoint returns `503` with per-service status, for example:

```json
{"postgres": "ok", "redis": "error"}
```

### Create a link

```bash
curl -i -X POST localhost:8000/api/links \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/some/very/long/path"}'
```

Response `201`:

```json
{
  "slug": "aB3dE9x",
  "short_url": "http://localhost:8000/aB3dE9x",
  "target_url": "https://example.com/some/very/long/path",
  "created_at": "2026-11-20T10:00:00Z",
  "expires_at": null
}
```

Optional fields:

```bash
curl -i -X POST localhost:8000/api/links \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com","custom_alias":"my-link","expires_in_seconds":3600}'
```

Validation rules:

- `url`: required, `http` or `https`, maximum 2048 characters.
- `custom_alias`: optional, 3-30 characters, `^[A-Za-z0-9_-]+$`, not reserved.
- `expires_in_seconds`: optional, 60 to 31,536,000 seconds.

Reserved aliases: `api`, `docs`, `redoc`, `openapi.json`, `health`, `admin`, `static`.

A duplicate custom alias returns `409`. Invalid input returns `422`. Random slug collisions are retried up to five times.

### Redirect

```bash
curl -i localhost:8000/<slug>
```

A valid link returns `302` with `Location: <target_url>`. A missing slug returns `404`; an expired link returns `410`.

### Get link info

```bash
curl -i localhost:8000/api/links/<slug>
```

Returns `200` with the same body shape as create. Missing links return `404`; expired links return `410`. This endpoint reads PostgreSQL directly and does not use Redis.

### Delete a link

```bash
curl -i -X DELETE localhost:8000/api/links/<slug>
```

Returns `204` with an empty body. The cache entry `link:<slug>` is invalidated. A missing slug returns `404`.

## Design decisions

### PostgreSQL is the source of truth

All link data lives in PostgreSQL. Redis only caches target URLs for redirects. If Redis is wiped or unavailable, the service still works.

### Redis is cache-aside, not read-through

The redirect path checks Redis first. On a miss, it reads PostgreSQL, writes the target URL to Redis with a TTL, and returns the redirect. The management API (`GET /api/links/{slug}`) reads PostgreSQL directly so it never serves stale cache data.

### 302 instead of 301

A `301` tells browsers to cache the redirect permanently. That would make deletion and expiry unreliable. `302` keeps redirects temporary so the server remains in control.

### The unique slug index is both a guard and a lookup path

The `slug` column is unique and indexed. Random slug generation does not check for existence first; it inserts and catches `IntegrityError`, retrying with a new slug up to five times. This avoids check-then-insert races and uses the database as the source of truth.

### TTL follows the link lifetime

A link that expires in 90 seconds gets a cache TTL of about 90 seconds. A link that never expires gets `DEFAULT_CACHE_TTL_SECONDS`. When the link expires, the cache entry expires with it, and the next lookup reaches PostgreSQL and returns `410`.

### Deletion invalidates the cache

After a successful database delete, the API deletes `link:{slug}` from Redis. If Redis invalidation fails, the delete still succeeds because PostgreSQL is authoritative; the cache entry expires at its TTL.

### Redis failures degrade gracefully

Every Redis operation catches exceptions, logs a warning, and falls back to PostgreSQL. A Redis outage makes redirects slower, not unavailable.

### Route ordering

`/health` and `/api/*` are registered before the `/{slug}` catch-all. The reserved alias list exists for the same reason: those names must not be shadowed by the redirect route.

## Verification

There is no automated test suite by design. The full manual acceptance checklist lives in `SPEC.md` section 12 and was run against the live application, including:

- Health checks for both services.
- Create, redirect, info, and delete flows.
- Cache population and cache-hit behavior.
- Custom alias conflicts and validation failures.
- Expiry after 60 seconds with Redis key removal.
- Redis outage fallback and `503` health response.
- The unique index on `slug`.

Compilation check:

```bash
.venv/bin/python -m compileall -q app
```

## Scope

This is a learning/portfolio project. The following are intentionally out of scope:

- Authentication or users
- Rate limiting
- Click analytics
- Alembic migrations
- Negative caching
- Automated tests
- A Dockerfile for the app itself

## Blog series

The `blog/` directory documents the build milestone by milestone:

- [Series index](blog/README.md)
- [Milestone 1: Designing the Foundation](blog/01-scaffold-and-health.md)
- [Milestone 2: Creating Links Without Racing Yourself](blog/02-creating-links.md)
- [Milestone 3: Redirects, Status Codes, and the Cost of Being Wrong](blog/03-redirects.md)
- [Milestone 4: Link Lifecycle](blog/04-link-lifecycle.md)
- [Milestone 5: Redis Cache-Aside](blog/05-redis-cache-aside.md)
- [Milestone 6: README, Acceptance Checks, and the Final Review](blog/06-polish-and-review.md)

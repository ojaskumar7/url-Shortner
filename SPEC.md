# SPEC: URL Shortener (FastAPI + PostgreSQL + Redis)

## 1. Overview
Build a URL shortening service. A client submits a long URL (optionally with a custom alias and an expiry) and gets back a short slug. Visiting `/{slug}` redirects to the original URL. PostgreSQL is the source of truth; Redis is a cache-aside layer for fast redirects.

This is a learning/portfolio project. **Keep it simple and readable.** Do not add features that are not listed in this spec.

### In scope
- Random Base62 slug generation with collision retry
- Custom aliases with validation and a reserved-word list
- Expiring links (TTL)
- Redis cache-aside for redirects
- REST API with input validation and consistent error responses
- `docker-compose.yml` that runs Postgres and Redis only

### Out of scope (do NOT build)
Auth/users, rate limiting, click analytics, Alembic migrations, negative caching, automated tests, a Dockerfile for the app itself.

## 2. Tech stack
- Python 3.12
- FastAPI, Uvicorn
- Pydantic v2, pydantic-settings
- SQLAlchemy 2.0 (async) + asyncpg
- redis-py (`redis.asyncio`)
- PostgreSQL 16, Redis 7 (via docker-compose)

`requirements.txt`:
```
fastapi
uvicorn[standard]
sqlalchemy[asyncio]
asyncpg
redis
pydantic-settings
```

## 3. Project structure
```
url-shortener/
  app/
    main.py            # app factory, lifespan (create tables, init/close Redis), router registration
    core/
      config.py        # Settings via pydantic-settings, reads .env
      database.py      # async engine, session factory, Base, get_session dependency
      redis.py         # Redis client creation + get_redis dependency
    models/
      link.py          # SQLAlchemy Link model
    schemas/
      link.py          # Pydantic request/response models
    services/
      base62.py        # Base62 alphabet + random slug generator
      link_service.py  # create/get/delete link logic, collision retry, alias checks
      cache.py         # Redis get/set/delete helpers for link:{slug}
    api/
      links.py         # /api/links routes
      redirect.py      # /{slug} route
      health.py        # /health route
  docker-compose.yml
  requirements.txt
  .env.example
  README.md
  SPEC.md
```

## 4. Configuration (`.env.example`)
```
DATABASE_URL=postgresql+asyncpg://shortener:shortener@localhost:5432/shortener
REDIS_URL=redis://localhost:6379/0
BASE_URL=http://localhost:8000
SLUG_LENGTH=7
DEFAULT_CACHE_TTL_SECONDS=86400
```
Load all of these through a single `Settings` class in `core/config.py`. No hardcoded values elsewhere.

## 5. Data model
Table `links`:

| Column      | Type                      | Notes                                   |
|-------------|---------------------------|-----------------------------------------|
| id          | BigInteger, PK, autoinc   |                                         |
| slug        | String(30), **unique, indexed**, not null | Case-sensitive. The unique index is both the collision guard and the fast lookup path. |
| target_url  | Text, not null            | Max 2048 chars (enforced in schema)     |
| created_at  | DateTime(timezone=True), not null | Server default `now()`          |
| expires_at  | DateTime(timezone=True), nullable | `NULL` means never expires      |

Create tables on app startup in the lifespan handler via `Base.metadata.create_all`. No migrations.

## 6. Slug generation (`services/base62.py`)
- Alphabet: `0-9A-Za-z` (62 characters).
- `generate_slug(length: int) -> str`: use `secrets.choice` to pick `length` characters. Default length 7 (62^7 ≈ 3.5 trillion).
- Keep this module pure (no DB or Redis imports) so it is easy to understand.

## 7. API contract

All error responses use FastAPI's default shape: `{"detail": "<message>"}`.

### 7.1 `POST /api/links` — create a link
Request body:
```json
{
  "url": "https://example.com/some/very/long/path",
  "custom_alias": "my-link",        // optional
  "expires_in_seconds": 3600        // optional
}
```
Validation:
- `url`: required, must be `http` or `https`, max 2048 characters. Reject anything else with 422.
- `custom_alias`: optional, 3-30 characters, regex `^[A-Za-z0-9_-]+$`. Must not be a reserved word (case-insensitive): `api`, `docs`, `redoc`, `openapi.json`, `health`, `admin`, `static`. Reserved or invalid alias → 422.
- `expires_in_seconds`: optional integer, min 60, max 31,536,000 (1 year). Omitted → link never expires.

Behavior:
- If `custom_alias` is given, use it as the slug. On unique-constraint conflict → **409** `"Alias already in use"`.
- Otherwise generate a random slug and insert. On `IntegrityError` (collision), roll back and retry with a new slug, up to **5 attempts**. If all fail → **500** `"Could not generate a unique slug"`.
- Compute `expires_at = now_utc + expires_in_seconds` when provided.
- Do NOT check-then-insert. Rely on the unique constraint and catch `IntegrityError` (this avoids race conditions).

Response **201**:
```json
{
  "slug": "aB3dE9x",
  "short_url": "http://localhost:8000/aB3dE9x",
  "target_url": "https://example.com/some/very/long/path",
  "created_at": "2026-11-19T18:30:00Z",
  "expires_at": "2026-11-19T19:30:00Z"
}
```
(`expires_at` is `null` when the link never expires. `short_url` = `BASE_URL` + `/` + slug.)

### 7.2 `GET /{slug}` — redirect
Flow (cache-aside):
1. Look up `link:{slug}` in Redis. **Hit** → return **302** to the cached URL.
2. **Miss** → query Postgres by slug.
   - Not found → **404** `"Link not found"`.
   - Found but `expires_at <= now` → **410** `"Link has expired"`.
   - Valid → write to Redis (see section 8), return **302** with `Location: <target_url>`.

Use **302** (not 301) so browsers do not cache the redirect and deletion/expiry take effect immediately.

### 7.3 `GET /api/links/{slug}` — link info
- 200 with the same body shape as the create response.
- 404 if missing, 410 if expired.
- This endpoint reads from Postgres directly (no Redis).

### 7.4 `DELETE /api/links/{slug}`
- Delete the row from Postgres, then delete `link:{slug}` from Redis.
- **204** on success, **404** if the slug does not exist.

### 7.5 `GET /health`
- Run `SELECT 1` on Postgres and `PING` on Redis.
- 200 `{"postgres": "ok", "redis": "ok"}` if both succeed.
- 503 with per-service status (e.g., `{"postgres": "ok", "redis": "error"}`) if either fails.

### Routing note
Register `/health` and `/api/*` routers **before** the `/{slug}` catch-all so they are never shadowed. The reserved-word list in 7.1 exists for the same reason.

## 8. Caching rules (`services/cache.py`)
- Key: `link:{slug}`. Value: the target URL string.
- TTL on write: the link's remaining lifetime in seconds (minimum 1) if `expires_at` is set; otherwise `DEFAULT_CACHE_TTL_SECONDS`.
- Because the TTL equals remaining lifetime, an expired link naturally drops out of Redis and the next lookup reaches Postgres, which returns 410.
- Delete invalidates the cache key.
- **Redis failures must not break redirects.** Wrap Redis calls in try/except, log a warning, and fall back to Postgres.
- No negative caching in v1.

## 9. Docker (`docker-compose.yml`)
Runs infrastructure only; the app runs locally with uvicorn.
- `postgres:16` — user/password/db all `shortener`, port 5432, named volume for data.
- `redis:7` — port 6379.

## 10. Code conventions
- Type hints everywhere; async functions for all I/O.
- Routers stay thin: validation and HTTP concerns in `api/`, logic in `services/`.
- Use FastAPI dependency injection for the DB session and Redis client.
- Docstrings on public functions; short comments explaining *why* for non-obvious logic (collision retry, cache TTL, route ordering).
- Use the `logging` module, not `print`.
- Use timezone-aware UTC datetimes everywhere.

## 11. Build order (stop after each milestone and summarize what was done)
1. **Scaffold:** folder structure, `requirements.txt`, `.env.example`, `docker-compose.yml`, config, DB engine, Link model, table creation on startup, `/health` (Postgres only for now).
2. **Create endpoint:** Base62 util, schemas with validation, `POST /api/links` with collision retry and custom alias handling.
3. **Redirect:** `GET /{slug}` straight from Postgres, with 404/410 handling.
4. **Info and delete:** `GET /api/links/{slug}` and `DELETE /api/links/{slug}`.
5. **Redis caching:** cache-aside in redirect, TTL logic, invalidation on delete, graceful Redis failure, Redis check in `/health`.
6. **Polish:** README (setup steps, curl examples, a short "design decisions" section), final review against the acceptance checklist.

## 12. Acceptance criteria (manual checklist)
Run `docker compose up -d`, then `uvicorn app.main:app --reload`, and verify:

1. `GET /health` returns 200 with both services `ok`.
2. Create a link:
   `curl -X POST localhost:8000/api/links -H "Content-Type: application/json" -d '{"url":"https://example.com"}'` → 201, 7-character slug.
3. `curl -i localhost:8000/<slug>` → 302 with the correct `Location`.
4. Second redirect request is served from Redis (confirm with `redis-cli GET link:<slug>` and the log output).
5. Custom alias works: create with `"custom_alias":"my-link"` → 201; repeat → 409.
6. Reserved alias (`"custom_alias":"api"`) → 422. Alias with invalid characters or length → 422.
7. Invalid URL (`ftp://x.com`, plain text, >2048 chars) → 422.
8. Expiry: create with `"expires_in_seconds":60`; redirect works immediately; after 60s redirect returns 410 and the Redis key is gone.
9. `GET /api/links/<slug>` returns the correct info; unknown slug → 404.
10. `DELETE /api/links/<slug>` → 204; afterwards redirect → 404 and `redis-cli GET link:<slug>` is empty.
11. Stop Redis (`docker compose stop redis`): redirects still work via Postgres, `/health` returns 503 with `"redis": "error"`.
12. `psql` → `\d links` shows the unique index on `slug`.

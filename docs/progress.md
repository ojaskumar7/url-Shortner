# Project Progress Log

Last updated: 2026-11-20

## Current Status

- **Milestone 1 complete:** scaffold, configuration, async database setup, `Link` model, startup table creation, and PostgreSQL-only `/health`.
- **Milestone 2 complete:** Base62 slug generation, request validation, custom aliases, collision retries, and `POST /api/links`.
- **Milestone 3 complete:** `GET /{slug}` redirects from PostgreSQL with `302`, `404`, and `410` handling.
- **Milestone 4 complete:** `GET /api/links/{slug}` returns metadata, and `DELETE /api/links/{slug}` removes links with `204` or `404`.
- **Milestone 5 complete:** Redis cache-aside redirects, delete invalidation, TTLs, and a PostgreSQL + Redis `/health`.
- **Milestone 6 complete:** README, full acceptance checklist, final scope review, and the closing blog post.
- **Status:** complete against `SPEC.md`.

Source of truth:

- `SPEC.md` — approved specification.
- `docs/plan.md` — full implementation plan with milestones, file map, verification steps, and commit points.

## Blog Series

- `blog/README.md` — series index and planned posts.
- `blog/01-scaffold-and-health.md` — Milestone 1 technical blog.
- `blog/02-creating-links.md` — Milestone 2 technical blog.
- `blog/03-redirects.md` — Milestone 3 technical blog.
- `blog/04-link-lifecycle.md` — Milestone 4 technical blog.
- `blog/05-redis-cache-aside.md` — Milestone 5 technical blog.
- `blog/06-polish-and-review.md` — Milestone 6 technical blog.

## Completed Work

### 1. Implementation plan

Created `docs/plan.md` covering:

- File responsibilities and interfaces.
- Six implementation milestones.
- Exact commands for manual verification.
- Commit points after each milestone.
- Final acceptance checklist.

Commit: `8849f09 chore: add implementation plan`

### 2. Milestone 1 — scaffold and PostgreSQL health

Created the project skeleton and core infrastructure:

- `requirements.txt` with FastAPI, Uvicorn, SQLAlchemy async, asyncpg, redis, and pydantic-settings.
- `.env.example` with all required configuration.
- `.gitignore` for `.venv/`, `.env`, bytecode, and cache files.
- `docker-compose.yml` running PostgreSQL 16 and Redis 7 only.
- `SPEC.md` containing the approved specification.
- `app/core/config.py` with a single `Settings` class.
- `app/core/database.py` with async engine, session factory, `Base`, and `get_session`.
- `app/models/link.py` with the `links` table model.
- `app/api/health.py` with a PostgreSQL-only health check for this milestone.
- `app/main.py` with lifespan table creation, logging, and router registration.
- Empty package markers under `app/`, `app/core/`, `app/models/`, `app/schemas/`, `app/services/`, and `app/api/`.

Commit: `17e7661 feat: scaffold app config database and health`

### 3. Milestone 2 — create endpoint

Added the first write path:

- `app/services/base62.py` with the `0-9A-Za-z` alphabet and `secrets.choice` slug generation.
- `app/schemas/link.py` with URL, alias, and expiry validation.
- `app/services/link_service.py` with create/get logic, UTC helpers, response mapping, custom alias conflict handling, and five-attempt random slug retry.
- `app/api/links.py` with `POST /api/links`.
- `app/main.py` now registers the links router after the health router.

Commit: `c31a64f feat: add link creation endpoint`

### 4. Milestone 3 — redirect endpoint

Added the read path:

- `app/api/redirect.py` with `GET /{slug}`.
- `app/main.py` now registers the redirect router last, after the health and links routers.
- Valid links return `302` with the target URL in `Location`.
- Missing slugs return `404 {"detail":"Link not found"}`.
- Expired links return `410 {"detail":"Link has expired"}`.
- Expiry is checked against timezone-aware UTC datetimes.

Commit: `270a2cb feat: add redirect endpoint`

### 5. Milestone 4 — link info and delete

Added the lifecycle endpoints:

- `app/services/link_service.py` now has `delete_link`, which deletes by slug and reports whether a row was removed.
- `app/api/links.py` now has `GET /api/links/{slug}` and `DELETE /api/links/{slug}`.
- Info returns the same response shape as create, with `404` for missing links and `410` for expired links.
- Delete returns `204` with an empty body, or `404` when the slug does not exist.
- Deleting an expired link is allowed and returns `204`.

Commit: `fbf813f feat: add link info and delete endpoints`

### 6. Milestone 5 — Redis cache-aside and full health

Added the cache layer:

- `app/core/redis.py` creates the async Redis client and exposes the `get_redis` dependency from `app.state.redis`.
- `app/services/cache.py` provides `cache_key`, `get_cached_target`, `set_cached_target`, and `delete_cached_target` with graceful failure handling.
- `app/main.py` initializes Redis during lifespan and closes it on shutdown.
- `app/api/redirect.py` checks Redis first, falls back to PostgreSQL on a miss, and populates the cache with a TTL based on the link lifetime.
- `app/api/links.py` invalidates `link:{slug}` after a successful delete.
- `app/api/health.py` checks PostgreSQL and Redis independently and returns `503` when either dependency is down.

Commit: `46d00a2 feat: add redis cache-aside and full health check`

### 7. Milestone 6 — README and final acceptance

Finished the project:

- Added `README.md` with setup, configuration, API examples, design decisions, scope, and the blog series index.
- Ran the full manual acceptance checklist from `SPEC.md` section 12 against the live application.
- Confirmed the schema has a unique index on `slug`.
- Confirmed no auth, rate limiting, analytics, Alembic, negative caching, automated tests, or app Dockerfile were added.
- Confirmed configuration stays in `Settings`, all request-path I/O is async, datetimes are timezone-aware UTC, errors use `{"detail": "..."}`, and `/health` plus `/api/*` are registered before `/{slug}`.
- Added the closing blog post and updated the series index.

Commit: `docs: add README and finalize project`

## Verification Performed

### Infrastructure and Milestone 1

Infrastructure:

```bash
docker compose up -d
docker compose ps
```

Result: PostgreSQL and Redis containers running on `5432` and `6379`.

PostgreSQL:

```bash
docker compose exec -T postgres pg_isready -U shortener -d shortener
```

Result: accepting connections.

Redis:

```bash
redis-cli -h localhost -p 6379 ping
```

Result: `PONG`.

Application health:

```bash
uvicorn app.main:app --port 8000
curl -i localhost:8000/health
```

Result:

```http
HTTP/1.1 200 OK
{"postgres":"ok"}
```

Note: this was the Milestone 1 behavior. `/health` now checks PostgreSQL and Redis independently.

Schema:

```bash
docker compose exec -T postgres psql -U shortener -d shortener -c '\d links'
```

Result:

```text
Indexes:
    "links_pkey" PRIMARY KEY, btree (id)
    "ix_links_slug" UNIQUE, btree (slug)
```

### Milestone 2

Manual verification was run against the live application with a temporary Python client script.

Result:

```text
random create: 201 slug=ZNqXDq6
custom alias create: 201 slug=my-link-1795113812
duplicate custom alias: 409
reserved/invalid aliases: 422
invalid URLs: 422
expiring create: 201 expires_at=2026-11-19T18:44:32.539899Z
```

Database check:

```bash
docker compose exec -T postgres psql -U shortener -d shortener \
  -c "SELECT slug, target_url, expires_at IS NOT NULL AS expiring FROM links ORDER BY id DESC LIMIT 5;"
```

Result:

```text
        slug        |               target_url                | expiring
--------------------+-----------------------------------------+----------
 4WcKJ2r            | https://example.com/expiring            | t
 my-link-1795113812 | https://example.com/alias               | f
 ZNqXDq6            | https://example.com/some/very/long/path | f
```

Collision retry check:

A temporary script monkeypatched the slug generator to force a collision on the first attempt, then return a unique slug. It also forced five consecutive collisions to verify the exhaustion path.

```text
collision retry success: collision-ok after 2 attempts
collision exhaustion: SlugGenerationError after 5 attempts
```

Compilation check:

```bash
.venv/bin/python -m compileall -q app
```

Result: `COMPILE_OK`.

### Milestone 3

A temporary HTTP client script verified the redirect path against the running application.

```text
redirect: 302 Location=https://example.com/redirect-target
unknown slug: 404 Link not found
expired slug: 410 Link has expired
route ordering: /health still returns 200
```

Server log confirmation:

```text
"GET /YWDdxLg HTTP/1.1" 302 Found
"GET /does-not-exist HTTP/1.1" 404 Not Found
"GET /expired-1795114092 HTTP/1.1" 410 Gone
"GET /health HTTP/1.1" 200 OK
```

The expired-link check inserted a row with `expires_at` in the past, then requested it through the public route. Temporary verification rows were removed afterward.

Compilation check:

```bash
.venv/bin/python -m compileall -q app
```

Result: `COMPILE_OK`.

### Milestone 4

A temporary HTTP client script verified info and deletion against the running application.

```text
info: 200 slug=OtHBGtV
unknown info: 404 Link not found
expired info: 410 Link has expired
delete: 204 empty body and row removed
after delete: info and redirect return 404
delete missing: 404
delete expired: 204 and row removed
```

Server log confirmation:

```text
"GET /api/links/OtHBGtV HTTP/1.1" 200 OK
"GET /api/links/does-not-exist HTTP/1.1" 404 Not Found
"GET /api/links/expired-info-1795149992 HTTP/1.1" 410 Gone
"DELETE /api/links/OtHBGtV HTTP/1.1" 204 No Content
"GET /api/links/OtHBGtV HTTP/1.1" 404 Not Found
"GET /OtHBGtV HTTP/1.1" 404 Not Found
"DELETE /api/links/OtHBGtV HTTP/1.1" 404 Not Found
"DELETE /api/links/expired-info-1795149992 HTTP/1.1" 204 No Content
```

The expired-link check inserted a row with `expires_at` in the past, confirmed `410` on info, then deleted it through the API and confirmed `204`. The temporary rows were removed afterward.

Compilation check:

```bash
.venv/bin/python -m compileall -q app
```

Result: `COMPILE_OK`.

### Milestone 5

A temporary HTTP and Redis client script verified cache-aside behavior against the running application.

```text
health: 200 postgres=ok redis=ok
cache populate: 302 and link:gJnAzjW ttl=86400
cache hit: 302 after direct database deletion
cache miss after eviction: 404
delete invalidation: 204, cache cleared, redirect 404
expiry ttl: key expired, redirect 410, ttl was 4
redis down: redirect 302 via PostgreSQL; health 503
redis restart: health 200; fallback link deleted
```

What each check proved:

- The first redirect populated `link:{slug}` with the target URL and a TTL of `86400`.
- The database row was deleted directly, then the second redirect still returned `302` from Redis. Removing the Redis key made the next request return `404`.
- Deleting through the API returned `204`, removed the Redis key, and made the next redirect return `404`.
- A link with a five-second lifetime cached with a four-second TTL. After six seconds the key was gone and the redirect returned `410`.
- With the Redis container stopped, a redirect still returned `302` via PostgreSQL. `/health` returned `503` with `{"postgres":"ok","redis":"error"}`.
- Restarting Redis restored `/health` to `200`.

Server log confirmation included the expected fallback warnings:

```text
WARNING app.services.cache: Redis GET failed for link:DggQrjK; falling back to PostgreSQL
WARNING app.services.cache: Redis SET failed for link:DggQrjK; continuing without cache
WARNING app.api.health: Redis health check failed
"GET /DggQrjK HTTP/1.1" 302 Found
"GET /health HTTP/1.1" 503 Service Unavailable
"GET /health HTTP/1.1" 200 OK
```

Compilation check:

```bash
.venv/bin/python -m compileall -q app
```

Result: `COMPILE_OK`.

### Milestone 6

The full `SPEC.md` section 12 acceptance checklist was run against the live application.

```text
1. health: 200 postgres=ok redis=ok
2. create: 201 slug=RkEHVEE
3. redirect: 302 with correct Location
4. cache hit: 302 after direct DB deletion (ttl=86400)
5. custom alias: 201 then duplicate 409 (acc-1795151556)
6. reserved and invalid aliases: 422
7. invalid URLs: 422
8. expiry: redirect 302, ttl=59; waiting 61s
8. expiry: Redis key gone, redirect/info 410
9. info: 200 and unknown 404
10. delete: 204, cache cleared, redirect 404, repeat 404
11. Redis down: redirect 302 via Postgres, health 503
ACCEPTANCE_OK
```

Schema check:

```text
Indexes:
    "links_pkey" PRIMARY KEY, btree (id)
    "ix_links_slug" UNIQUE, btree (slug)
```

Final review checks:

- `requirements.txt` contains only the six specified dependencies.
- No `Dockerfile`, Alembic directory, or test suite exists.
- `.env` is ignored by `.gitignore`; `.env.example` is tracked.
- No hardcoded app configuration exists outside `Settings`.
- No synchronous HTTP or database calls exist in the request path.
- `utc_now()` uses `datetime.now(timezone.utc)` and timestamp columns use `DateTime(timezone=True)`.
- API errors use FastAPI `HTTPException`, producing `{"detail": "..."}`.
- `app/main.py` registers `health`, `links`, then the redirect catch-all.
- Redis failure fallback and `503` health behavior were verified live.

Compilation check:

```bash
.venv/bin/python -m compileall -q app
```

Result: `COMPILE_OK`.

## Environment Notes

- Native systemd services `postgresql@16-main` and `redis-server` are active again and occupy `5432` and `6379`.
- The Docker Compose containers were started with a temporary override on `5433`/`6380` for Milestones 4, 5, and 6 verification. The override lives in `/tmp/opencode/docker-compose.override.yml` and is not part of the repository.
- Milestone 5 verification stopped and restarted the Redis container to confirm that redirects degrade to PostgreSQL and `/health` reports `503`.
- The local `.env` points to the standard ports. `.env` is gitignored and is not committed.
- To use the standard ports again, stop the native services (requires sudo) and run `docker compose up -d`.
- `AGENTS.md` is currently deleted in the working tree. It was left untouched and is not part of the completed commits.

## How to Run the Current State

```bash
source .venv/bin/activate
docker compose up -d
uvicorn app.main:app --reload
```

If native services occupy `5432`/`6379`, stop them first or run against the temporary override with `DATABASE_URL=postgresql+asyncpg://shortener:shortener@localhost:5433/shortener` and `REDIS_URL=redis://localhost:6380/0`.

Check health:

```bash
curl -i localhost:8000/health
```

Expected: `200 {"postgres":"ok","redis":"ok"}`.

Create a link:

```bash
curl -i -X POST localhost:8000/api/links \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com"}'
```

Expected: `201` with a 7-character slug.

Follow the redirect:

```bash
curl -i localhost:8000/<slug>
```

Expected: `302` with `Location: <target_url>`.

Get link info:

```bash
curl -i localhost:8000/api/links/<slug>
```

Expected: `200` with the same shape as the create response.

Delete a link:

```bash
curl -i -X DELETE localhost:8000/api/links/<slug>
```

Expected: `204` with an empty body. A subsequent redirect or info request returns `404`.

## Final State

- All six milestones are complete.
- `README.md` documents setup, API usage, and design decisions.
- The full `SPEC.md` acceptance checklist passes.
- The blog series is complete through Milestone 6.

## Pending Milestones

- [x] Milestone 1 — scaffold and PostgreSQL health
- [x] Milestone 2 — create endpoint
- [x] Milestone 3 — redirect from PostgreSQL
- [x] Milestone 4 — link info and delete
- [x] Milestone 5 — Redis cache-aside and full health check
- [x] Milestone 6 — README and final acceptance review

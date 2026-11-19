# Project Progress Log

Last updated: 2026-11-20

## Current Status

- **Milestone 1 complete:** scaffold, configuration, async database setup, `Link` model, startup table creation, and PostgreSQL-only `/health`.
- **Milestone 2 complete:** Base62 slug generation, request validation, custom aliases, collision retries, and `POST /api/links`.
- **Next:** Milestone 3 — `GET /{slug}` redirects from PostgreSQL with `404` and `410` handling.

Source of truth:

- `SPEC.md` — approved specification.
- `docs/plan.md` — full implementation plan with milestones, file map, verification steps, and commit points.

## Blog Series

- `blog/README.md` — series index and planned posts.
- `blog/01-scaffold-and-health.md` — Milestone 1 technical blog.
- `blog/02-creating-links.md` — Milestone 2 technical blog.

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

Note: `/health` currently checks PostgreSQL only. The Redis check is added in Milestone 5.

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

## Environment Notes

- Native systemd services `postgresql@16-main` and `redis-server` were occupying `5432` and `6379`.
- Those services were stopped, and the Docker Compose stack now owns `5432` and `6379`.
- The local `.env` points to the standard ports. `.env` is gitignored and is not committed.
- `AGENTS.md` is currently deleted in the working tree. It was left untouched and is not part of the completed commits.
- A temporary Compose override was used earlier for ports `5433`/`6380`; it is not part of the repository.

## How to Run the Current State

```bash
source .venv/bin/activate
docker compose up -d
uvicorn app.main:app --reload
```

Check health:

```bash
curl -i localhost:8000/health
```

Expected: `200 {"postgres":"ok"}`.

Create a link:

```bash
curl -i -X POST localhost:8000/api/links \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com"}'
```

Expected: `201` with a 7-character slug.

## Next Milestone

### Milestone 3 — redirect from PostgreSQL

Planned files:

- `app/api/redirect.py`
- Modify `app/main.py`

Planned behavior:

- `GET /{slug}` looks up the slug in PostgreSQL.
- Missing slug returns `404 {"detail":"Link not found"}`.
- Expired link returns `410 {"detail":"Link has expired"}`.
- Valid link returns `302` with `Location: <target_url>`.
- The redirect router is registered after `/health` and `/api/*` so the catch-all does not shadow fixed routes.

After implementation:

```bash
curl -i localhost:8000/<slug>
```

Expected: `302` with the correct `Location`.

## Pending Milestones

- [x] Milestone 1 — scaffold and PostgreSQL health
- [x] Milestone 2 — create endpoint
- [ ] Milestone 3 — redirect from PostgreSQL
- [ ] Milestone 4 — link info and delete
- [ ] Milestone 5 — Redis cache-aside and full health check
- [ ] Milestone 6 — README and final acceptance review

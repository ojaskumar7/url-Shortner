# Project Progress Log

Last updated: 2026-11-20

## Current Status

- **Milestone 1 complete:** scaffold, configuration, async database setup, `Link` model, startup table creation, and PostgreSQL-only `/health`.
- **Next:** Milestone 2 — Base62 slug generation, validation schemas, and `POST /api/links`.

Source of truth:

- `SPEC.md` — approved specification.
- `docs/plan.md` — full implementation plan with milestones, file map, verification steps, and commit points.

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

## Verification Performed

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

## Next Milestone

### Milestone 2 — create endpoint

Planned files:

- `app/services/base62.py`
- `app/schemas/link.py`
- `app/services/link_service.py`
- `app/api/links.py`
- Modify `app/main.py`

Planned behavior:

- Random Base62 slug generation with `secrets.choice`.
- URL validation: `http`/`https`, max 2048 characters.
- Custom alias validation: 3–30 characters, `^[A-Za-z0-9_-]+$`, reserved-word rejection.
- Expiry validation: 60 seconds to 31,536,000 seconds.
- `POST /api/links` returns `201`.
- Custom alias conflict returns `409 "Alias already in use"`.
- Random slug collision retries up to 5 times, then returns `500 "Could not generate a unique slug"`.
- Response shape: `slug`, `short_url`, `target_url`, `created_at`, `expires_at`.

After implementation:

```bash
curl -i -X POST localhost:8000/api/links \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com"}'
```

Expected: `201` with a 7-character slug.

## Pending Milestones

- [x] Milestone 1 — scaffold and PostgreSQL health
- [ ] Milestone 2 — create endpoint
- [ ] Milestone 3 — redirect from PostgreSQL
- [ ] Milestone 4 — link info and delete
- [ ] Milestone 5 — Redis cache-aside and full health check
- [ ] Milestone 6 — README and final acceptance review

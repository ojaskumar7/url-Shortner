# Milestone 6: README, Acceptance Checks, and the Final Review

Milestone 6 is not a feature milestone. It is the point where the project has to prove it matches the spec, not just that the happy path works.

The work was:

- Write the README with setup, API examples, and design decisions.
- Run the full manual acceptance checklist from `SPEC.md`.
- Review the implementation for scope creep, configuration leaks, synchronous I/O, timezone mistakes, and route-ordering problems.
- Record the final state and stop.

## The acceptance checklist is the real test

The project deliberately has no automated test suite. That is a scope decision, not a claim that tests are unimportant. For this learning build, the acceptance checklist is the contract:

```text
GET /health -> 200 with postgres and redis ok
POST /api/links -> 201 with a 7-character slug
GET /{slug} -> 302 with the correct Location
second redirect served from Redis
custom alias -> 201, duplicate -> 409
reserved and invalid aliases -> 422
invalid URLs -> 422
expiry -> 302 immediately, 410 after 60 seconds, Redis key gone
GET /api/links/{slug} -> 200, unknown -> 404
DELETE /api/links/{slug} -> 204, cache cleared, redirect -> 404
Redis down -> redirect still 302, health 503
psql \d links -> unique index on slug
```

All of those passed against the running application.

The expiry check was the slowest one because it followed the spec literally: create a link with `expires_in_seconds: 60`, redirect immediately, wait 61 seconds, then verify that the redirect and info endpoints returned `410` and that the Redis key was gone. That is a better test than shortening the TTL in code, because it exercises the same path a real client would use.

The cache-hit check was the most important one. After the first redirect populated Redis, the database row was deleted directly, and a second redirect still returned `302` from the cached target. Removing the Redis key made the next request return `404`. That proves the second request was served from Redis rather than PostgreSQL.

## Final review findings

The review checked the constraints that are easy to accidentally violate:

- **No out-of-scope features.** There is no auth, rate limiting, analytics, Alembic, negative caching, automated test suite, or app Dockerfile.
- **No hardcoded app configuration.** Database URL, Redis URL, base URL, slug length, and default TTL all come from `Settings`.
- **Async I/O.** Database access uses async SQLAlchemy; Redis access uses `redis.asyncio`. There are no synchronous HTTP or database calls in the request path.
- **Timezone-aware UTC.** `utc_now()` uses `datetime.now(timezone.utc)`, and both timestamp columns are `DateTime(timezone=True)`.
- **Consistent errors.** API failures use FastAPI's `HTTPException`, which produces `{"detail": "..."}`.
- **Route order.** `/health` and `/api/*` are registered before the `/{slug}` catch-all.
- **Graceful Redis failure.** Redirects fall back to PostgreSQL when Redis is unavailable, and `/health` reports the degraded dependency.
- **Secret hygiene.** `.env` is ignored; `.env.example` is committed.
- **Schema.** The `links` table has a unique index on `slug`.

## What the project demonstrates

The final system is small, but it contains the important boundaries of a real service:

- **Source of truth versus cache.** PostgreSQL owns the data; Redis is disposable.
- **Database-enforced uniqueness.** Collision handling is a constraint plus retry, not a check-then-insert race.
- **TTL as a consistency tool.** Cache entries expire with the links they represent.
- **Invalidation on write.** Delete removes both the row and the cache key.
- **Failure isolation.** Redis outages degrade latency, not correctness.
- **Health semantics.** A `503` can mean degraded, not dead.
- **Route precedence.** A catch-all route is a design hazard if fixed paths are not registered first.

Those are the same questions that show up in much larger systems. The scale changes; the boundaries do not.

## What I would do next in production

The current design is intentionally incomplete. A production version would add:

- Automated unit and integration tests.
- Alembic migrations instead of `create_all` on startup.
- Authentication and ownership checks for create/delete.
- Rate limiting on create and redirect.
- Metrics for cache hit rate, miss rate, invalidation failures, and redirect latency.
- Redis connect and command timeouts so a slow Redis cannot stall requests.
- Cache stampede protection for hot links.
- A strategy for the cache-aside delete race: versioned keys, locks, or short TTLs.
- Soft delete or an audit trail if link recovery matters.
- A deployment story: container image, process manager, and health probes.

None of those belong in this version. The spec explicitly excludes most of them, and adding them would obscure the core design lessons.

## The final state

The repository now has:

- A runnable FastAPI service with PostgreSQL and Redis.
- A Docker Compose file for infrastructure only.
- A README with setup and API examples.
- A specification and implementation plan.
- A progress log with verification results and commit history.
- A six-part technical blog series.

The project is complete against the spec. The next step is not more code; it is deciding which production concern is worth adding first.

## Series

- [Series index](README.md)
- Previous: [Milestone 5 — Redis Cache-Aside, TTLs, and Failing Open](05-redis-cache-aside.md)

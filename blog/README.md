# URL Shortener: A System Design Learning Series

This series documents the construction of a small URL shortener as a system design exercise. The goal is not just to make it work, but to explain the decisions behind the code: where state lives, where failures are handled, what is cached, what is intentionally left out, and how the design would change under real traffic.

Each post covers one milestone from `docs/plan.md`.

## Posts

- [Milestone 1: Designing the Foundation](01-scaffold-and-health.md) — FastAPI, async SQLAlchemy, PostgreSQL, the Redis boundary, configuration, lifespan, and health checks.
- Milestone 2: Creating Links — Base62 slugs, validation, custom aliases, and collision retries. Planned.
- Milestone 3: Redirects — cache-free redirects, expiry semantics, and HTTP status codes. Planned.
- Milestone 4: Link Lifecycle — reading metadata and deleting links safely. Planned.
- Milestone 5: Redis Cache-Aside — TTLs, invalidation, and graceful degradation. Planned.
- Milestone 6: Polish and Review — README, acceptance checks, and design retrospective. Planned.

## Ground Rules

- PostgreSQL is the source of truth.
- Redis is a cache, never the system of record.
- The implementation stays deliberately small: no auth, analytics, rate limiting, migrations, or automated tests in this version.
- Every milestone ends with a runnable system and a manual verification pass.

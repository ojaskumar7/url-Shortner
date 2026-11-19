# Milestone 1: Designing the Foundation of a URL Shortener

A URL shortener looks simple from the outside: take a long URL, return a short one, redirect later. The interesting part is not the happy path. It is deciding where the truth lives, what happens when a dependency is unavailable, and how much complexity the system actually needs.

This is the first post in a series documenting that design. Milestone 1 is intentionally unglamorous: configuration, a database connection, one table, a health endpoint, and local infrastructure. The goal is to create a foundation that makes the later milestones boring in the best way.

The full specification lives in `SPEC.md`, and the implementation plan lives in `docs/plan.md`.

## The architecture in one picture

The final system has two backing services, but they play very different roles:

```text
Client
  |
  v
FastAPI application
  |             \
  |              \  cache-aside reads/writes
  v               v
PostgreSQL       Redis
source of truth  disposable cache
```

PostgreSQL owns the data. If Redis disappears, redirects should still work, just more slowly. That single rule shapes the cache design, the health check, and the failure handling in later milestones.

For Milestone 1, only the left side exists: FastAPI and PostgreSQL. Redis is already defined in `docker-compose.yml`, but the application does not use it yet. That is deliberate. Adding infrastructure before the code needs it makes it too easy to hide design problems behind a cache.

## Decision 1: PostgreSQL is the source of truth

A URL shortener has one critical mapping:

```text
slug -> target URL
```

Everything else is metadata: creation time, expiry time, and later perhaps ownership or analytics. The mapping must survive process restarts, cache eviction, and Redis outages. That makes PostgreSQL the natural source of truth.

The `links` table is small on purpose:

```python
class Link(Base):
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

The `slug` column is the important one. It is both a lookup key and a uniqueness constraint. Later, when random slug generation collides with an existing row, the database will reject the insert and the application will retry. There is no need for a separate check-then-insert flow, which would introduce a race condition under concurrent requests.

The unique index also gives us the fast lookup path for redirects. One index serves two purposes: correctness and performance.

`expires_at` is nullable. `NULL` means the link never expires, which is simpler than storing a sentinel date and avoids special cases in queries.

## Decision 2: Async all the way down

The application uses SQLAlchemy 2.0's async engine with `asyncpg`:

```python
engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
SessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)
```

A URL shortener is I/O-bound. Most of the request time is spent waiting on a database or cache. Async does not make an individual query faster, but it lets the process handle other requests while waiting. For a learning project, it also forces us to be explicit about where I/O happens.

The session dependency creates one session per request:

```python
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield one async database session per request."""
    async with SessionLocal() as session:
        yield session
```

This keeps transaction boundaries visible. Service functions receive a session and decide when to commit or roll back. Routers do not reach into the engine directly.

`expire_on_commit=False` is a small but useful choice. After a commit, the ORM normally expires loaded attributes and refreshes them on next access. In async code that refresh can trigger an implicit I/O operation at an awkward time. Keeping attributes usable after commit makes response construction predictable.

## Decision 3: One Settings object

Configuration is loaded through a single `Settings` class:

```python
class Settings(BaseSettings):
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
```

The important part is not the class itself. It is the rule that no other module reads environment variables directly. If the database URL changes, there is one place to look. If the base URL for generated short links changes, there is one source of truth.

This also makes local development and deployment behave the same way: both provide the same named variables, just with different values.

## Decision 4: Lifespan, not import-time side effects

The application creates tables during startup using FastAPI's lifespan context:

```python
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
```

There are two alternatives here, and both are worse for this project:

- Creating tables at import time makes the module hard to test and hides when database work happens.
- Running a migration tool is the right production choice, but the spec explicitly excludes Alembic to keep the learning surface small.

Lifespan gives us a clear startup phase and a clear shutdown phase. The shutdown phase disposes the connection pool, which matters when the process is restarted repeatedly during development.

`Base.metadata.create_all` is not a migration system. It creates missing tables and indexes, but it does not alter existing ones. That is an acceptable tradeoff for a greenfield learning project, and an important limitation to name rather than hide.

## Decision 5: A health endpoint is an API too

The first version of `/health` checks PostgreSQL only:

```python
@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    """Report whether PostgreSQL is reachable."""
    await session.execute(text("SELECT 1"))
    return {"postgres": "ok"}
```

A health check is not just a debugging convenience. It is an operational contract. It answers a specific question: can this process do its job right now?

For this milestone, the job depends on PostgreSQL, so the endpoint checks PostgreSQL. In Milestone 5, Redis becomes part of the redirect path, so the endpoint will report both services and return `503` when either is unavailable. The check will stay dependency-aware rather than becoming a generic "is the process alive?" ping.

There is a useful distinction between liveness and readiness:

- **Liveness:** is the process running and not deadlocked?
- **Readiness:** can the process serve useful traffic?

`/health` here is closer to readiness. A process with no database connection is alive but not useful.

## Docker Compose only runs infrastructure

The application runs locally with Uvicorn, while Docker Compose runs PostgreSQL and Redis:

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

Keeping the app out of Docker shortens the feedback loop. Uvicorn reloads on file changes, logs are immediately visible, and debugging does not require rebuilding an image. The backing services are the parts that benefit most from containerization: fixed versions, isolated data, and repeatable setup.

The named volume is important. Without it, `docker compose down` would remove the database and every shortened link with it. A cache can be disposable; the source of truth should not be.

## A real-world snag: ports are a shared resource

During verification, ports `5432` and `6379` were already occupied by native system services, not by the Docker containers. The containers could not bind to the host ports, and the application could not reach the expected database.

The fix was operational rather than architectural:

```bash
sudo systemctl stop postgresql@16-main redis-server
docker compose up -d
```

The lesson is worth recording: "my service is running" and "my service is reachable on the port I expect" are different claims. Checking the listener is part of the deployment:

```bash
ss -ltnp '( sport = :5432 or sport = :6379 )'
```

This is also why the health endpoint exists. It tests the dependency from the application's point of view instead of assuming that a container is healthy because it exists.

## Verification

The milestone was considered complete only after the running system was checked, not after the files were written.

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

The unique index is the part to notice. It is the database-level guarantee that makes collision handling possible without a race-prone pre-check.

## Tradeoffs and what I would change in production

The current design is intentionally small, but it is not pretending to be production-complete.

- **`create_all` instead of migrations.** Fine for a greenfield learning project. In production, schema changes need versioned migrations and rollback plans.
- **No connection pool tuning.** The defaults are reasonable for local work. Under load, pool size, timeouts, and statement caching need measurement.
- **No retry policy around PostgreSQL.** The app fails fast when the database is unavailable. A production system might add bounded retries and circuit breaking, but only after defining what failure mode it is protecting against.
- **No secrets management.** Credentials come from `.env` for local development. Production should use a secret manager or platform-provided credentials.
- **One health endpoint.** A larger deployment might split liveness and readiness, and expose metrics separately.

None of these are missing by accident. They are deferred because the spec is explicit about scope, and because adding them now would obscure the core design questions.

## What comes next

Milestone 2 adds the first real behavior: random Base62 slug generation, request validation, custom aliases, and `POST /api/links`.

That is where the foundation starts paying off. The unique index becomes a collision guard. The session dependency becomes the transaction boundary. The response schema becomes the contract between the API and its clients.

The foundation is not the interesting part of a system. It is what makes the interesting parts safe to build.

## Series

- [Series index](README.md)
- Next: Milestone 2 — Creating Links

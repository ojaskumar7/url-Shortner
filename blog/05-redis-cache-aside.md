# Milestone 5: Redis Cache-Aside, TTLs, and Failing Open

The redirect path is the hot path. Every click on a short link pays for it, so this milestone puts Redis in front of PostgreSQL for `GET /{slug}`.

The important part is not that Redis is fast. It is that Redis is optional. PostgreSQL remains the source of truth, and the system keeps working when Redis does not.

## The cache-aside flow

The redirect route now does this:

```text
GET /{slug}
    |
    +--> Redis GET link:{slug}
    |       |
    |       +-- hit --> 302 to cached target
    |
    +--> PostgreSQL lookup
            |
            +-- missing --> 404
            +-- expired --> 410
            +-- valid --> Redis SET link:{slug} with TTL
                          |
                          +--> 302 to target
```

In code:

```python
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

This is cache-aside: the application owns the cache. Redis does not know about links, and PostgreSQL does not know about Redis. The application reads the cache first, falls back to the database on a miss, and writes the result back into the cache.

That keeps the failure boundary clear. Redis is an optimization, not a dependency for correctness.

## Decision 1: only redirects use the cache

`GET /api/links/{slug}` still reads PostgreSQL directly. So does deletion.

That is deliberate. The management API answers questions about the current state of a link. Serving those answers from a cache would make the API inherit cache staleness, and it would make a temporary Redis outage look like missing links to clients that are trying to inspect or delete them.

The cache exists for one job: making redirects cheap. It is not a general read-through layer.

## Decision 2: TTL follows the link lifetime

A cached entry must never outlive the link it represents. The TTL is computed from the link:

```python
def cache_ttl_seconds(link: Link) -> int:
    """Return the Redis TTL for a valid link, at least one second."""
    if link.expires_at is None:
        return settings.DEFAULT_CACHE_TTL_SECONDS
    remaining = int((link.expires_at - utc_now()).total_seconds())
    return max(1, remaining)
```

A link with no expiry gets the default 24-hour TTL. A link that expires in 90 seconds gets a 90-second cache entry. A link with less than a second left gets a one-second entry, which is the smallest TTL Redis will accept for this purpose.

This is what lets the cache hit path skip the expiry check. If the cached entry exists, it was written while the link was valid, and its TTL was bounded by the link's remaining lifetime. When the link expires, the cache entry expires with it.

There is still a small clock-skew assumption here. In a distributed system, the application and Redis may not agree perfectly on time. The one-second floor and the fact that expiry is checked again on a database miss keep the window small. A stricter design could store the expiry timestamp in the cached value and compare it on every hit.

## Decision 3: deletion invalidates the cache

Deleting a link now has two steps:

```text
DELETE /api/links/{slug}
    |
    +--> PostgreSQL delete
    |
    +--> Redis DEL link:{slug}
```

The delete route only invalidates after the database delete succeeds:

```python
deleted = await link_service.delete_link(session, slug)
if not deleted:
    raise HTTPException(status_code=404, detail="Link not found")
await cache.delete_cached_target(redis, slug)
return Response(status_code=status.HTTP_204_NO_CONTENT)
```

If the database delete fails, the link still exists, so the cache entry is still valid. If the database delete succeeds but Redis invalidation fails, the cache entry may survive until its TTL. The spec accepts that tradeoff: Redis failures must not break deletes.

The alternative would be to fail the delete when Redis cannot be reached. That would make Redis a hard dependency for a write path, which is exactly what the design is trying to avoid.

## Decision 4: Redis failures are warnings, not errors

Every cache operation catches exceptions:

```python
async def get_cached_target(redis: Redis, slug: str) -> str | None:
    try:
        return await redis.get(cache_key(slug))
    except Exception:
        logger.warning("Redis GET failed; falling back to PostgreSQL", exc_info=True)
        return None
```

The redirect path then behaves as if it had a cache miss. It reads PostgreSQL, tries to populate the cache, and returns the redirect even if the cache write also fails.

This is "failing open": the system degrades to the slower path instead of failing the request. For a URL shortener, a slow redirect is better than no redirect.

The same principle applies to deletion. A failed cache invalidation is logged, but the API still returns `204` because the source-of-truth row is gone.

## Decision 5: health reports both dependencies

`/health` now checks PostgreSQL and Redis independently:

```python
statuses = {"postgres": "ok", "redis": "ok"}

try:
    await session.execute(text("SELECT 1"))
except Exception:
    statuses["postgres"] = "error"

try:
    await redis.ping()
except Exception:
    statuses["redis"] = "error"

status_code = 200 if all(value == "ok" for value in statuses.values()) else 503
return JSONResponse(status_code=status_code, content=statuses)
```

When both are healthy:

```json
{"postgres": "ok", "redis": "ok"}
```

When Redis is down but PostgreSQL is up:

```json
{"postgres": "ok", "redis": "error"}
```

with HTTP `503`.

That `503` is useful for operators and load balancers, but it does not mean redirects are down. It means the service is degraded. The redirect path is designed to keep working through that degradation.

## The race that cache invalidation does not solve

Cache-aside has a classic race:

```text
Request A: cache miss
Request A: read link from PostgreSQL
Request B: delete link from PostgreSQL
Request B: delete link:{slug} from Redis
Request A: write old target into Redis
```

The delete has already invalidated the key, but Request A writes the old value afterward. The cache now contains a link that no longer exists, until the TTL expires.

This implementation does not try to eliminate that race. The TTL bounds the damage, and the spec accepts stale reads until expiry when invalidation fails. A production system could reduce the window with:

- A short TTL for redirects.
- Versioned cache keys or a generation counter.
- A delete-after-write retry.
- A distributed lock around cache fills.
- A database change stream that invalidates keys.

Each of those adds complexity. The right choice depends on how much stale redirect traffic the product can tolerate.

There is also no negative caching. A missing or expired link is not written to Redis, so repeated requests for bad slugs still hit PostgreSQL. That is intentional: negative caching adds invalidation problems of its own, and the spec excludes it.

## Verification

The cache path was tested against the running application:

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

The cache-hit test is the important one. After the first redirect populated Redis, the database row was deleted directly. A second redirect still returned `302` from the cached target. Deleting the Redis key then produced `404`, confirming that the second request had been served from cache rather than PostgreSQL.

The expiry test inserted a link with a five-second lifetime. The first redirect populated Redis with a four-second TTL. After waiting six seconds, the key was gone and the redirect returned `410`.

The failure test stopped the Redis container. Redirects continued to return `302` through PostgreSQL, while `/health` returned `503` with `"redis":"error"`. Restarting Redis restored a `200` health response.

## Tradeoffs and what I would change in production

- **Stale cache race.** A concurrent redirect can repopulate a deleted key until TTL. Mitigations are versioning, locks, or shorter TTLs.
- **No stampede protection.** Many simultaneous misses can all query PostgreSQL and write the same key.
- **No negative caching.** Repeated 404s and 410s always hit PostgreSQL.
- **No timeout tuning.** The Redis client uses library defaults. Production should set connect and command timeouts so a slow Redis cannot stall requests.
- **No cache metrics.** Hit rate, miss rate, and invalidation failures should be measured.
- **No cache warming.** A cold cache sends all traffic to PostgreSQL.
- **Hard dependency on TTL correctness.** The cache-hit path trusts the TTL to enforce expiry.

The most important design property is that none of these make Redis the system of record. If Redis is wiped, the service still works. If Redis is down, the service still works. It is just slower.

## What comes next

Milestone 6 is the final pass: a README, the full acceptance checklist, and a review of the design against the spec.

## Series

- [Series index](README.md)
- Previous: [Milestone 4 — Link Lifecycle, Deletion, and the Cache Problem Ahead](04-link-lifecycle.md)
- Next: Milestone 6 — README and Final Acceptance Review

# Milestone 4: Link Lifecycle, Deletion, and the Cache Problem Ahead

Milestone 3 made short links useful. Milestone 4 makes them manageable: clients can inspect a link and delete it.

The new endpoints are small:

```python
@router.get("/{slug}", response_model=LinkResponse)
async def get_link_info(...):
    ...

@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_link(...):
    ...
```

But the lifecycle rules are where the system design starts to show. A link can be created, read, expired, deleted, or missing. Those states need consistent behavior across the redirect path and the management API.

## Decision 1: the info endpoint is a read model

`GET /api/links/{slug}` returns the same response shape as `POST /api/links`:

```json
{
  "slug": "Rltdfdh",
  "short_url": "http://localhost:8000/Rltdfdh",
  "target_url": "https://example.com/info-target",
  "created_at": "2026-11-20T00:22:00.123456Z",
  "expires_at": null
}
```

That consistency is deliberate. A client that creates a link already knows how to parse the response. There is no second schema to learn for reads.

The endpoint reads PostgreSQL directly. Redis is not involved yet, and that is intentional. The management API should reflect the source of truth, not a cache that may be stale or temporarily unavailable.

The expiry behavior matches the redirect path:

- Missing slug: `404`.
- Expired link: `410`.

An expired link is still a known resource. It is gone in the sense that it no longer redirects, but the row still exists. `410` communicates that distinction.

## Decision 2: delete is a hard delete

Deleting a link removes the row:

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

This is a hard delete, not a soft delete. There is no `deleted_at` column and no tombstone. The spec does not ask for an audit trail or recovery, so adding those would be scope creep.

Hard deletion has a clear consequence: after the row is gone, the slug can be reused. That is a product decision. A system that wants permanent slug ownership would need a separate reserved-slug table or a soft-delete flag. This project does not.

Expiry is separate from deletion. An expired link still occupies its slug until someone deletes it. That is why `GET /api/links/{slug}` can return `410` instead of `404` for an expired row.

## Decision 3: 204 on delete, 404 when missing

The delete endpoint returns:

- `204 No Content` when a row was deleted.
- `404 Not Found` when the slug does not exist.

A `204` response has no body, which is why the route returns a bare `Response`:

```python
return Response(status_code=status.HTTP_204_NO_CONTENT)
```

The `404` choice is a contract decision. Some APIs treat `DELETE` as idempotent and return `204` even if the resource was already gone. This spec chooses `404`, which tells the caller that the requested slug was not present at the time of the request. Both designs are defensible; the important thing is that the behavior is explicit and consistent.

Deleting an expired link still returns `204`. Expiry affects redirects and info reads, not the ability to remove the row. This keeps the lifecycle operations predictable: deletion is about existence, not validity.

## Decision 4: deletion is simple until a cache exists

At this milestone, deletion has one backing store:

```text
DELETE /api/links/{slug}
        |
        v
   PostgreSQL
```

That makes the consistency story easy. After the commit, the next redirect lookup finds no row and returns `404`.

Milestone 5 changes that. Once Redis caches `link:{slug}`, deletion has two writes:

```text
DELETE /api/links/{slug}
        |
        +--> PostgreSQL delete
        |
        +--> Redis delete
```

Now there are new failure modes:

- The database delete succeeds but Redis invalidation fails.
- Redis is unavailable entirely.
- A redirect repopulates the cache between the database delete and the cache delete.

The spec's answer is that Redis failures must not break redirects or deletes. The cache is allowed to be stale until its TTL expires if invalidation fails. That is a deliberate availability-over-perfect-consistency tradeoff, and it is worth naming before implementing it.

This is why the management API reads PostgreSQL directly. It should not inherit cache staleness when answering "does this link exist?"

## Decision 5: the current delete has a small race

The implementation follows a read-then-delete pattern:

```python
link = await get_link(session, slug)
if link is None:
    return False
await session.delete(link)
await session.commit()
```

That is easy to read, but it is not atomic. Two concurrent deletes can both read the row before either commits. The first delete wins; the second may see a stale ORM object and produce a `StaleDataError` instead of a clean `404`.

For this learning project, the simple version is acceptable and the spec does not test concurrent deletes. A production implementation would collapse the operation into a single statement:

```python
result = await session.execute(delete(Link).where(Link.slug == slug))
await session.commit()
return result.rowcount > 0
```

That version lets the database decide whether a row was actually removed. It is one round trip, atomic, and returns the correct `404` for the losing request.

This is a good example of a general system design rule: when correctness depends on current state, prefer a single database operation over a read followed by a write.

## Verification

The lifecycle was tested against the running application:

```text
info: 200 slug=Rltdfdh
unknown info: 404 Link not found
expired info: 410 Link has expired
delete: 204 empty body
after delete: info and redirect return 404
delete missing: 404
delete expired: 204
```

Server log:

```text
"GET /api/links/Rltdfdh HTTP/1.1" 200 OK
"GET /api/links/does-not-exist HTTP/1.1" 404 Not Found
"GET /api/links/expired-info-1795114320 HTTP/1.1" 410 Gone
"DELETE /api/links/Rltdfdh HTTP/1.1" 204 No Content
"GET /api/links/Rltdfdh HTTP/1.1" 404 Not Found
"GET /Rltdfdh HTTP/1.1" 404 Not Found
"DELETE /api/links/Rltdfdh HTTP/1.1" 404 Not Found
"DELETE /api/links/expired-info-1795114320 HTTP/1.1" 204 No Content
```

The expired link was inserted with `expires_at` in the past. It returned `410` for info, then `204` for deletion. That confirms expiry and deletion are separate concerns.

## Tradeoffs and what I would change in production

- **No soft delete.** Recovery and auditability would require a tombstone or a separate history table.
- **No idempotent delete.** The current contract returns `404` for an already-deleted link. Some clients prefer `204`.
- **Read-then-delete race.** A single `DELETE ... WHERE slug = ...` with `rowcount` would be atomic.
- **No cache invalidation yet.** That arrives with Redis in Milestone 5.
- **No bulk operations.** Deleting many links one request at a time is fine for this scale.
- **No authorization.** Anyone who knows a slug can inspect or delete it. The spec excludes auth, but a real service would need ownership checks.
- **No audit log.** A production system may want to record who deleted what and when.

The cache invalidation issue is the next real design step. Once a read cache exists, every write path has to answer a harder question: what happens if the cache and the database disagree?

## What comes next

Milestone 5 introduces Redis as a cache-aside layer for redirects. It adds TTLs, invalidation on delete, graceful Redis failure, and a full health check.

That is where the system starts to trade consistency for latency, and where the decisions made in this milestone become operationally visible.

## Series

- [Series index](README.md)
- Previous: [Milestone 3 — Redirects, Status Codes, and the Cost of Being Wrong](03-redirects.md)
- Next: Milestone 5 — Redis Cache-Aside

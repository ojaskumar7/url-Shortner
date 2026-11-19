# Milestone 3: Redirects, Status Codes, and the Cost of Being Wrong

Milestone 2 made it possible to create a short link. Milestone 3 makes the link useful: `GET /{slug}` looks up the target and sends the client there.

The implementation is small:

```python
@router.get("/{slug}", response_class=RedirectResponse)
async def redirect_to_target(
    slug: str,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Redirect a short slug to its target URL."""
    link = await link_service.get_link(session, slug)
    if link is None:
        raise HTTPException(status_code=404, detail="Link not found")
    if link_service.is_expired(link):
        raise HTTPException(status_code=410, detail="Link has expired")
    return RedirectResponse(url=link.target_url, status_code=302)
```

The interesting decisions are not in the line count. They are in the status codes, the route ordering, and the fact that this milestone deliberately reads PostgreSQL on every request.

## Decision 1: use 302, not 301

A redirect is a contract with the client. The status code determines how long that contract lasts.

- `301 Moved Permanently` tells browsers and intermediaries that the redirect is permanent and can be cached aggressively.
- `302 Found` tells them the redirect is temporary.
- `307 Temporary Redirect` is also temporary, but preserves the original HTTP method.
- `308 Permanent Redirect` is the permanent, method-preserving variant.

This service needs `302`. Short links can be deleted, and they can expire. If a browser caches a `301`, it may keep redirecting after the row is gone. That makes deletion and expiry feel broken even when the server is correct.

`307` would also be temporary, but the redirect is for a `GET`. There is no request body or method semantics to preserve. `302` is the simplest status that communicates "temporary" for this use case.

The status code is part of the product behavior, not just a protocol detail.

## Decision 2: distinguish 404 from 410

There are two ways a slug can fail to redirect:

- It never existed.
- It existed, but it expired.

The first returns `404 Not Found`. The second returns `410 Gone`.

Both are errors, but they mean different things. `404` says "I have no resource at this location." `410` says "this resource existed and is intentionally no longer available." That distinction is useful to clients, monitoring systems, and humans reading logs.

The expired row is not deleted in this milestone. The database still contains it, and the read path compares `expires_at` to the current UTC time:

```python
def is_expired(link: Link, now: datetime | None = None) -> bool:
    """Return whether a link has an expiration time at or before now."""
    if link.expires_at is None:
        return False
    return link.expires_at <= (now or utc_now())
```

Keeping the row makes the `410` possible and preserves the record. A background cleanup job could eventually remove expired rows, but that is not part of this version.

The comparison is timezone-aware on both sides. The database stores `TIMESTAMP WITH TIME ZONE`, and `utc_now()` returns an aware UTC datetime. Mixing naive and aware datetimes is a common source of subtle expiry bugs.

## Decision 3: route ordering is a correctness requirement

The redirect route is a catch-all:

```python
@router.get("/{slug}")
```

That pattern matches any single path segment. It will happily match `/health` and `/docs` if those routes are not registered first.

Starlette, the routing layer under FastAPI, evaluates routes in order. The fixed routes must win before the catch-all gets a chance:

```python
app.include_router(health.router)
app.include_router(links.router)

# The catch-all must be registered after all fixed paths.
app.include_router(redirect.router)
```

This is why the reserved alias list from Milestone 2 exists as well. Even with correct route ordering, a user-created alias named `health` would create a confusing second meaning for that path. Rejecting reserved aliases keeps the URL namespace unambiguous.

The verification checked that `/health` still returned `200` after the catch-all was added. That is a route-ordering test disguised as a health check.

## Decision 4: read from PostgreSQL first

This milestone does not use Redis. Every redirect performs a database lookup by slug:

```python
async def get_link(session: AsyncSession, slug: str) -> Link | None:
    """Look up a link by slug in PostgreSQL."""
    result = await session.execute(select(Link).where(Link.slug == slug))
    return result.scalar_one_or_none()
```

That is intentionally the slow version. It establishes the baseline behavior before a cache is introduced. If the redirect is wrong without a cache, adding Redis will only make it wrong faster.

The unique index on `links.slug` makes this lookup efficient. The same index that guards against collisions during writes is the index used for reads. One data structure, two responsibilities.

`scalar_one_or_none()` also encodes an assumption: the unique constraint guarantees at most one row for a slug. If that assumption ever breaks, the query fails loudly instead of silently picking one row.

## Decision 5: errors use the API's normal shape

The redirect endpoint raises `HTTPException` for both failure cases:

```python
raise HTTPException(status_code=404, detail="Link not found")
raise HTTPException(status_code=410, detail="Link has expired")
```

FastAPI turns those into the same JSON error shape used by the rest of the API:

```json
{"detail": "Link not found"}
```

That consistency matters for clients. A redirect endpoint still has an API contract when it fails. A client should not need a different error parser for `/{slug}` than it uses for `/api/links`.

## Verification

The redirect path was tested against the running application:

```text
redirect: 302 Location=https://example.com/redirect-target
unknown slug: 404 Link not found
expired slug: 410 Link has expired
route ordering: /health still returns 200
```

The server log confirmed the status codes:

```text
"GET /YWDdxLg HTTP/1.1" 302 Found
"GET /does-not-exist HTTP/1.1" 404 Not Found
"GET /expired-1795114092 HTTP/1.1" 410 Gone
"GET /health HTTP/1.1" 200 OK
```

The expiry test inserted a row with `expires_at` in the past, then requested it through the public route. That exercises the same comparison the real read path uses without waiting a full minute for a short TTL.

## Tradeoffs and what I would change in production

The current design is correct but deliberately naive in a few places:

- **Every redirect hits PostgreSQL.** That is the baseline this project will optimize in Milestone 5. The cache will be added only after the uncached behavior is correct.
- **No negative caching.** Unknown slugs will hit the database every time. Negative caching can reduce that load, but it also creates invalidation problems when a slug is later created.
- **No click analytics.** The spec excludes analytics, and adding write amplification to the read path would change the design significantly.
- **No open-redirect protection beyond URL validation.** The service is supposed to redirect to external URLs, so this is inherent. A production service might add malware scanning, domain reputation checks, or abuse reporting.
- **No HEAD-specific behavior.** The route handles `GET`; a production service might define explicit `HEAD` semantics for link checkers and crawlers.
- **No cache headers.** Clients and proxies get the default behavior. A production service would think carefully about `Cache-Control` and whether `410` responses should be cached.
- **No metrics.** Redirect latency, hit rate, and `404`/`410` rates are the natural signals for this endpoint.

The biggest production decision would be whether a `410` should be treated as permanent by clients. The status code says it is gone, but the service might still allow an expired alias to be reused later. That policy needs to be explicit before adding aggressive caching.

## What comes next

Milestone 4 adds the management side of the link lifecycle: `GET /api/links/{slug}` for metadata and `DELETE /api/links/{slug}` for removal.

That introduces a new kind of consistency problem. The redirect path reads from PostgreSQL, and deletion will eventually need to invalidate Redis as well. For now, deletion is straightforward: remove the row and let the next redirect return `404`.

## Series

- [Series index](README.md)
- Previous: [Milestone 2 — Creating Links Without Racing Yourself](02-creating-links.md)
- Next: Milestone 4 — Link Lifecycle

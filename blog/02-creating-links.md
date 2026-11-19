# Milestone 2: Creating Links Without Racing Yourself

Milestone 1 gave us a healthy process and a table. Milestone 2 gives the system its first real job: accept a long URL and turn it into a short slug.

The interesting design problem is not generating a random string. It is making the write path correct when two requests want the same slug at the same time. That is where a lot of simple-looking systems quietly break.

The implementation lives in:

- `app/services/base62.py`
- `app/schemas/link.py`
- `app/services/link_service.py`
- `app/api/links.py`

## The API contract

The endpoint accepts three fields:

```json
{
  "url": "https://example.com/some/very/long/path",
  "custom_alias": "my-link",
  "expires_in_seconds": 3600
}
```

Only `url` is required. A successful response returns the slug, the full short URL, the original target, and timestamps:

```json
{
  "slug": "ZNqXDq6",
  "short_url": "http://localhost:8000/ZNqXDq6",
  "target_url": "https://example.com/some/very/long/path",
  "created_at": "2026-11-20T00:13:32.539899Z",
  "expires_at": null
}
```

There are three distinct failure modes:

- Invalid input returns `422`.
- A custom alias that already exists returns `409`.
- Exhausting the random slug retry budget returns `500`.

Each one represents a different kind of problem: bad client data, a conflict with existing state, or a system-level failure.

## Decision 1: random Base62 slugs

The slug generator is deliberately small:

```python
ALPHABET = string.digits + string.ascii_uppercase + string.ascii_lowercase


def generate_slug(length: int = 7) -> str:
    """Return a random Base62 slug of the requested length."""
    return "".join(secrets.choice(ALPHABET) for _ in range(length))
```

Base62 gives us `0-9`, `A-Z`, and `a-z`. At seven characters, the space is:

```text
62^7 = 3,521,614,606,208
```

Roughly 3.5 trillion possible slugs. That is large enough that collisions are rare, but not impossible. Randomness also makes slugs hard to enumerate, which is a useful property for a public shortener.

The alternative is a sequential ID encoded in Base62. That guarantees uniqueness and makes collision handling disappear, but it leaks creation order and requires a centralized sequence. For a learning project, random generation is a better exercise because it forces us to handle the failure mode explicitly.

## Decision 2: let the database enforce uniqueness

The naive implementation is:

```python
existing = await get_link(session, slug)
if existing is not None:
    slug = generate_slug()
await session.commit()
```

That has a race condition. Two requests can both check the same slug, both see that it is free, and both attempt to insert it. The database is the only component that can arbitrate that race correctly.

So the service does not pre-check. It inserts and catches the unique-constraint violation:

```python
for _ in range(MAX_SLUG_ATTEMPTS):
    link = Link(
        slug=generate_slug(settings.SLUG_LENGTH),
        target_url=payload.url,
        expires_at=expires_at,
    )
    session.add(link)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        logger.warning("Slug collision detected; retrying with a new slug")
        continue
    await session.refresh(link)
    return link

raise SlugGenerationError
```

The unique index on `links.slug` is doing real work here. It is not just a performance optimization. It is the correctness boundary.

The rollback matters too. After an `IntegrityError`, the SQLAlchemy session is in a failed state. It cannot be reused for another insert until the transaction is rolled back. Retrying without the rollback would turn a recoverable collision into a confusing session error.

The loop is capped at five attempts. With a 3.5 trillion-character space, five consecutive collisions are not a realistic event for this project. The cap exists to avoid an infinite loop if something is fundamentally wrong, such as a broken generator or a misconfigured slug length.

## Decision 3: custom aliases are a different contract

A custom alias uses the same uniqueness constraint, but the user-facing behavior is different. The client asked for a specific name, so a conflict is a client-visible conflict, not an internal retry:

```python
if payload.custom_alias is not None:
    link = Link(
        slug=payload.custom_alias,
        target_url=payload.url,
        expires_at=expires_at,
    )
    session.add(link)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise AliasConflictError from exc
    await session.refresh(link)
    return link
```

The router maps that exception to:

```http
HTTP/1.1 409 Conflict
{"detail":"Alias already in use"}
```

This is the same database guarantee as random slugs, but a different policy. A random collision is our problem to solve. A requested alias collision is the caller's problem to resolve.

Custom aliases also need validation before they reach the database:

- 3 to 30 characters.
- Only letters, numbers, hyphens, and underscores.
- Not a reserved word such as `api`, `health`, `docs`, or `admin`.

The reserved-word rule is not arbitrary. The redirect route is a catch-all `/{slug}`. If someone could create an alias named `health`, it would compete with the health endpoint. The reserved list prevents that class of routing ambiguity at the data boundary.

Slugs remain case-sensitive. `My-Link` and `my-link` are different rows. That is a deliberate simplification. Case-insensitive aliases would require a normalized lookup key or a functional index, and the spec does not ask for it.

## Decision 4: validate at the edge

The Pydantic schema is the boundary between untrusted JSON and trusted service code:

```python
class LinkCreate(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    custom_alias: str | None = Field(default=None, min_length=3, max_length=30)
    expires_in_seconds: int | None = Field(default=None, ge=60, le=31_536_000)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("URL must be a valid http or https URL")
        return value
```

The URL validator rejects `ftp://`, plain text, and anything without a scheme and network location. The `Field` constraints reject URLs over 2048 characters and aliases outside the allowed length.

The expiry bounds are a design decision, not a technical limit. Sixty seconds is a reasonable minimum for a short-lived link. One year is a reasonable maximum for this project. Without bounds, a client could create a link that expires in the past or far beyond any useful horizon.

Validation at the edge keeps the service functions simpler. By the time `create_link` runs, the URL is a valid HTTP(S) URL and the alias has already passed the format and reserved-word checks.

## Decision 5: time is UTC or it is a bug

Expiry is computed once, in UTC:

```python
expires_at = None
if payload.expires_in_seconds is not None:
    expires_at = utc_now() + timedelta(seconds=payload.expires_in_seconds)
```

`utc_now()` returns `datetime.now(timezone.utc)`. The database column uses `DateTime(timezone=True)`. There is no naive datetime anywhere in the write path.

This matters because expiry is a comparison, and comparisons between naive and aware datetimes raise errors. It also matters because the server may run in a different timezone from the client. Storing UTC and converting at the presentation layer is the boring, reliable choice.

`created_at` is a database default:

```python
created_at: Mapped[datetime] = mapped_column(
    DateTime(timezone=True),
    server_default=func.now(),
    nullable=False,
)
```

Using the database clock for creation time avoids trusting the application process clock for a value that belongs to the row. The application refreshes the object after commit so the response contains the database-generated timestamp.

## Decision 6: derive the short URL, do not store it

The response includes `short_url`, but the database does not have a column for it. It is derived:

```python
short_url=f"{settings.BASE_URL.rstrip('/')}/{link.slug}"
```

The base URL is deployment configuration, not link data. If the service moves from localhost to a domain, every stored row should not need to change. Deriving the URL keeps the schema smaller and avoids a second source of truth.

The `rstrip("/")` handles a trailing slash in configuration, so both `http://localhost:8000` and `http://localhost:8000/` produce the same result.

## Verification

The milestone was verified against the running application, not just by inspecting the code.

Random creation:

```text
random create: 201 slug=ZNqXDq6
```

Custom alias:

```text
custom alias create: 201 slug=my-link-1795113812
duplicate custom alias: 409
```

Validation:

```text
reserved/invalid aliases: 422
invalid URLs: 422
```

Expiry:

```text
expiring create: 201 expires_at=2026-11-19T18:44:32.539899Z
```

The database confirmed the rows:

```text
        slug        |               target_url                | expiring
--------------------+-----------------------------------------+----------
 4WcKJ2r            | https://example.com/expiring            | t
 my-link-1795113812 | https://example.com/alias               | f
 ZNqXDq6            | https://example.com/some/very/long/path | f
```

The `expiring` column is `true` only for the link created with `expires_in_seconds`.

## Tradeoffs and what I would change in production

The current design is correct for the spec, but there are deliberate limits:

- **Random collisions are retried, not eliminated.** Five attempts is plenty for seven Base62 characters. A shorter slug length would need a larger retry budget or a pre-generated slug pool.
- **No idempotency key.** Sending the same request twice creates two links. A production API might accept an idempotency key for create operations.
- **No alias normalization.** Case-sensitive aliases are simple and predictable, but a product decision could require case-insensitive names.
- **No rate limiting.** The spec excludes it, but a public endpoint that writes rows needs abuse protection.
- **No reservation system for custom aliases.** The unique constraint resolves races, but a high-traffic product might want explicit alias reservations or a separate namespace.
- **No metrics.** Collision retries and 409 rates are exactly the kind of signals worth emitting in production.

If this were a real service, I would also consider pre-generating slugs in batches, measuring collision rates, and deciding whether custom aliases need their own table or namespace. Those are scaling decisions, not requirements for the learning version.

## What comes next

Milestone 3 adds the read path: `GET /{slug}` redirects to the target URL, returns `404` for unknown slugs, and returns `410` for expired links.

That is where the data model starts to matter operationally. A slug is only useful if the redirect is fast, and expiry is only correct if every read path agrees on what "expired" means.

## Series

- [Series index](README.md)
- Previous: [Milestone 1 — Designing the Foundation](01-scaffold-and-health.md)
- Next: Milestone 3 — Redirects

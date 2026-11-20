import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health, links, redirect
from app.core.database import Base, engine
from app.core.redis import create_redis_client
from app.models import link  # noqa: F401  (registers the Link model)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create tables, initialize Redis, and release resources on shutdown."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables ready")
    app.state.redis = create_redis_client()
    try:
        yield
    finally:
        await app.state.redis.aclose()
        await engine.dispose()


app = FastAPI(title="URL Shortener", lifespan=lifespan)

# Register fixed paths before the /{slug} catch-all added later.
app.include_router(health.router)
app.include_router(links.router)

# The catch-all must be registered after all fixed paths.
app.include_router(redirect.router)

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health
from app.core.database import Base, engine
from app.models import link  # noqa: F401  (registers the Link model)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


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


app = FastAPI(title="URL Shortener", lifespan=lifespan)

# Register fixed paths before the /{slug} catch-all added later.
app.include_router(health.router)

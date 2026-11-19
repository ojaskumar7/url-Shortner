from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and `.env`."""

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


settings = Settings()

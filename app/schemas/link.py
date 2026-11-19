import re
from datetime import datetime
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

RESERVED_ALIASES = {
    "api",
    "docs",
    "redoc",
    "openapi.json",
    "health",
    "admin",
    "static",
}
ALIAS_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class LinkCreate(BaseModel):
    """Validated payload for creating a short link."""

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

    @field_validator("custom_alias")
    @classmethod
    def validate_custom_alias(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not ALIAS_PATTERN.fullmatch(value):
            raise ValueError(
                "custom_alias may contain only letters, numbers, hyphens, and underscores"
            )
        if value.lower() in RESERVED_ALIASES:
            raise ValueError("custom_alias is reserved")
        return value


class LinkResponse(BaseModel):
    """Public representation of a short link."""

    model_config = ConfigDict(from_attributes=True)

    slug: str
    short_url: str
    target_url: str
    created_at: datetime
    expires_at: datetime | None

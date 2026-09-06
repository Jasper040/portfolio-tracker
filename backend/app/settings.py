"""Application settings. Required secrets are validated at startup, not at use."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed configuration.

    `database_url` has no default on purpose: a missing database URL must fail at
    startup with a clear message rather than silently defaulting to a scratch file
    that quietly accumulates a second, wrong ledger.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    base_currency: str = "EUR"
    lot_method: str = "FIFO"
    cors_origins: str = "http://localhost:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        """Browser origins allowed to call the API.

        A comma-separated string rather than a `list[str]` field: pydantic-settings
        parses list-typed fields as JSON, so the obvious
        `CORS_ORIGINS=http://localhost:5173,http://localhost:5174` in a .env fails
        to parse and the error points at JSON rather than at the comma. Splitting a
        plain string accepts what people actually type.

        This is configurable at all because the dev server does not reliably get
        the port it asks for -- Vite silently falls back to 5174 when 5173 is
        taken, and the resulting CORS rejection surfaces in the UI only as a bare
        "Failed to fetch".
        """
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

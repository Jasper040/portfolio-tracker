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


@lru_cache
def get_settings() -> Settings:
    return Settings()

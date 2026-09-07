"""Application settings. Required secrets are validated at startup, not at use."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

#: `backend/app/settings.py` -> `backend/app` -> `backend` -> the repo.
_REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Environment-backed configuration.

    `database_url` has no default on purpose: a missing database URL must fail at
    startup with a clear message rather than silently defaulting to a scratch file
    that quietly accumulates a second, wrong ledger.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    lot_method: str = "FIFO"
    cors_origins: str = "http://localhost:5173"
    #: Where the operator's corporate-action answers live (design doc Sec 6.3).
    #: Ledger-adjacent rather than in the database on purpose: it is a hand-written
    #: record of decisions, so it wants to be readable and diffable.
    #:
    #: Absolute, anchored on the repo rather than on the working directory. The API
    #: is started from `backend/` and the CLI is run from wherever the operator
    #: happens to be, and a relative default breaks in the worst possible way: an
    #: unreadable resolutions file answers nothing, so the import refuses and
    #: reports the corporate actions as unanswered -- while the file sits there,
    #: answered, one directory up.
    corporate_actions_path: str = str(_REPO_ROOT / "config" / "corporate_actions.yaml")
    #: The operator's symbol answers (M2 spec section 6.3). Ledger-adjacent and
    #: gitignored for the same reason `corporate_actions.yaml` is: a key here is
    #: an ISIN, which is a holding.
    instrument_symbols_path: str = str(_REPO_ROOT / "config" / "instrument_symbols.yaml")
    #: Hand-maintained prices for instruments no provider covers (parent doc Sec 8.2).
    manual_prices_path: str = str(_REPO_ROOT / "config" / "manual_prices.csv")

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

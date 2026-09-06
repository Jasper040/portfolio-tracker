from __future__ import annotations

from collections.abc import Sequence

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Engine

from app.api import routes_transactions
from app.db import create_engine_and_tables
from app.settings import get_settings

# Used only when a caller injects an engine without naming origins -- see below.
DEFAULT_CORS_ORIGINS: tuple[str, ...] = ("http://localhost:5173",)


def create_app(
    engine: Engine | None = None,
    cors_origins: Sequence[str] | None = None,
) -> FastAPI:
    """Build the application.

    Settings are read only on the path that actually needs them. A caller that
    injects an engine -- every test does -- is not going through a browser, so
    making it supply a DATABASE_URL it never uses, purely so CORS could be
    configured, would be a tax on the whole suite for no benefit.
    """
    if engine is None:
        settings = get_settings()
        engine = create_engine_and_tables(settings.database_url)
        if cors_origins is None:
            cors_origins = settings.cors_origin_list

    app = FastAPI(title="Portfolio Tracker")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cors_origins if cors_origins is not None else DEFAULT_CORS_ORIGINS),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.engine = engine
    app.include_router(routes_transactions.router)
    return app

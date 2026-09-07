from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Engine

from app.api import (
    routes_instrument,
    routes_lots,
    routes_positions,
    routes_transactions,
    routes_valuation,
)
from app.db import create_engine_and_tables
from app.ingest.benchmarks import Benchmark, load_benchmarks
from app.settings import get_settings

# Used only when a caller injects an engine without naming origins -- see below.
DEFAULT_CORS_ORIGINS: tuple[str, ...] = ("http://localhost:5173",)


def create_app(
    engine: Engine | None = None,
    cors_origins: Sequence[str] | None = None,
    benchmarks: Sequence[Benchmark] | None = None,
) -> FastAPI:
    """Build the application.

    Settings are read only on the path that actually needs them. A caller that
    injects an engine -- every test does -- is not going through a browser, so
    making it supply a DATABASE_URL it never uses, purely so CORS or the
    benchmark set could be configured, would be a tax on the whole suite for no
    benefit. Such a caller gets an empty benchmark set by default -- the same
    "absent is a legitimate state" answer `load_benchmarks` gives a missing
    file -- and can pass its own tuple to test comparison behaviour.
    """
    if engine is None:
        settings = get_settings()
        engine = create_engine_and_tables(settings.database_url)
        if cors_origins is None:
            cors_origins = settings.cors_origin_list
        if benchmarks is None:
            benchmarks = load_benchmarks(Path(settings.benchmarks_path))

    app = FastAPI(title="Portfolio Tracker")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cors_origins if cors_origins is not None else DEFAULT_CORS_ORIGINS),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.engine = engine
    app.state.benchmarks = tuple(benchmarks) if benchmarks is not None else ()
    app.include_router(routes_transactions.router)
    app.include_router(routes_lots.router)
    app.include_router(routes_valuation.router)
    app.include_router(routes_positions.router)
    app.include_router(routes_instrument.router)
    return app

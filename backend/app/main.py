from __future__ import annotations

import logging
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
from app.ingest.benchmarks import Benchmark, BenchmarkConfigError, load_benchmarks
from app.settings import get_settings

# Used only when a caller injects an engine without naming origins -- see below.
DEFAULT_CORS_ORIGINS: tuple[str, ...] = ("http://localhost:5173",)


_log = logging.getLogger(__name__)


def load_benchmarks_or_warn(path: Path) -> tuple[Benchmark, ...]:
    """The benchmark set, or none of it, but never a startup traceback.

    The CLI names a malformed benchmark file and exits 2. `create_app` used to
    let `BenchmarkConfigError` escape, so a typo in one optional config file
    took the ledger, the valuation and the positions table down with it.

    Refusing to start would be the wrong repair. M3 section 4.4 already settled
    the principle for the fetch path -- a wrong benchmark must not cost the
    instrument phase its five-year backfill -- and an absent benchmark set is a
    state this app already supports, being exactly what a missing file gives.
    The comparison does not render; nothing else notices.

    Degrading is not swallowing. The warning names the file and the reason,
    because the failure is otherwise invisible until someone wonders where their
    benchmark line went.
    """
    try:
        return tuple(load_benchmarks(path))
    except BenchmarkConfigError as malformed:
        _log.warning(
            "%s ignored: %s. The app starts without a benchmark set, and the "
            "instrument comparison will not render until this is fixed.",
            path,
            malformed,
        )
        return ()


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
            benchmarks = load_benchmarks_or_warn(Path(settings.benchmarks_path))

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

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Engine

from app.api import routes_transactions
from app.db import create_engine_and_tables
from app.settings import get_settings


def create_app(engine: Engine | None = None) -> FastAPI:
    app = FastAPI(title="Portfolio Tracker")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.engine = engine or create_engine_and_tables(get_settings().database_url)
    app.include_router(routes_transactions.router)
    return app

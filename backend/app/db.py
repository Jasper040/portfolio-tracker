"""Engine and schema creation."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, event
from sqlmodel import Session, SQLModel, create_engine

import app.models.ledger  # noqa: F401  - registers tables on SQLModel.metadata


def create_engine_and_tables(database_url: str) -> Engine:
    engine = create_engine(database_url, echo=False)
    if engine.dialect.name == "sqlite":
        _enforce_sqlite_foreign_keys(engine)
    SQLModel.metadata.create_all(engine)
    return engine


def get_session(engine: Engine) -> Session:
    return Session(engine)


def _enforce_sqlite_foreign_keys(engine: Engine) -> None:
    """SQLite ignores declared foreign keys unless explicitly asked not to.

    Without this the local database accepts rows Postgres would reject — an orphan
    transaction pointing at a deleted import_batch, say — so the suite would be
    validating weaker semantics than the deployment target, and a referential bug
    would surface only in production. The schema is portable; the enforcement has
    to be too.
    """

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

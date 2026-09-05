"""Engine and schema creation."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, event, make_url
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import app.models.ledger  # noqa: F401  - registers tables on SQLModel.metadata


def create_engine_and_tables(database_url: str) -> Engine:
    engine = create_engine(database_url, echo=False, **_sqlite_memory_kwargs(database_url))
    if engine.dialect.name == "sqlite":
        _enforce_sqlite_foreign_keys(engine)
    SQLModel.metadata.create_all(engine)
    return engine


def _sqlite_memory_kwargs(database_url: str) -> dict[str, Any]:
    """A `:memory:` SQLite database lives inside a single DBAPI connection.

    SQLAlchemy's default pool for `sqlite://`/`sqlite:///:memory:` hands out one
    connection per calling thread, so a thread that did not create the schema sees
    its own empty database. An ASGI test client (FastAPI's `TestClient` included)
    dispatches request handlers onto a worker thread distinct from the one that
    built the engine, so without this every request would 404 against tables that
    "don't exist". `StaticPool` plus `check_same_thread=False` keeps the one open
    connection alive and safe to share across threads; it is a no-op for the
    single-threaded callers already covered by the existing test suite, and it
    does not apply to file-backed SQLite or Postgres.
    """
    url = make_url(database_url)
    if url.drivername == "sqlite" and url.database in (None, "", ":memory:"):
        return {"poolclass": StaticPool, "connect_args": {"check_same_thread": False}}
    return {}


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

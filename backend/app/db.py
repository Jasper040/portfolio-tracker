"""Engine and schema creation."""

from __future__ import annotations

from sqlalchemy import Engine
from sqlmodel import SQLModel, create_engine

import app.models.ledger  # noqa: F401  - registers tables on SQLModel.metadata


def create_engine_and_tables(database_url: str) -> Engine:
    engine = create_engine(database_url, echo=False)
    SQLModel.metadata.create_all(engine)
    return engine

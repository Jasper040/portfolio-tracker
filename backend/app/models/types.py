"""Exact-Decimal column type.

SQLAlchemy's `Numeric` on SQLite round-trips through float, which reintroduces the
cent-level drift the Decimal discipline exists to prevent. Storing the canonical
string keeps SQLite exact, while Postgres still gets a real NUMERIC column, so the
schema stays portable without giving up exactness locally.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import Dialect, Numeric, String, TypeDecorator


class DecimalString(TypeDecorator[Decimal]):
    impl = String
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Numeric(28, 10))
        return dialect.type_descriptor(String(40))

    def process_bind_param(self, value: Decimal | None, dialect: Dialect) -> Any:
        if value is None:
            return None
        if not isinstance(value, Decimal):
            raise TypeError(
                f"money columns take Decimal, got {type(value).__name__}: {value!r}. "
                "This column type is the last boundary where the no-float rule can "
                "still be enforced; coercing here would defeat its whole purpose."
            )
        return value if dialect.name == "postgresql" else str(value)

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        return value if isinstance(value, Decimal) else Decimal(str(value))

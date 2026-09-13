"""Exact-Decimal column type.

SQLAlchemy's `Numeric` round-trips through float on SQLite, which reintroduces the
cent-level drift the Decimal discipline exists to prevent -- so SQLite stores the
canonical string. A `Numeric(28, 10)` column on Postgres has the opposite problem:
Postgres *pads* a stored value to the column's declared scale, so
`Decimal("502.1500")` comes back as `Decimal("502.1500000000")`. Since
`TransactionOut` serialises money with `str(value)`, the same ledger would then
render a different string locally and on Postgres, and the storage layer's scale
invariant (`str(txn.price_local) == "502.1500"`, exponent `-4`) would fail there.

So the canonical string is stored on both dialects: this type's whole purpose is
exactness, and scale is part of what "exact" means for a value that came from a
broker statement, not part of a numeric quantity to be normalised away.

Trade-off, recorded deliberately: this gives up DB-side numeric aggregation
(SQL `SUM`/`AVG`) on Postgres too. That is acceptable because the design puts
analytics in pandas over derived tables (design doc Sec 4.1), never in SQL over
the ledger itself.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import Dialect, String, TypeDecorator

#: How STALE the prices behind an answer are (design doc Sec 8.1). Defined here
#: rather than in `api/schemas.py` so `analytics/` can enforce the invariant
#: without importing from the API layer -- nothing under analytics/, domain/,
#: ingest/ or providers/ depends on app.api, and this type is not a reason to
#: start.
Coverage = Literal["missing", "partial", "manual", "full"]

#: How much of a requested SPAN a series actually reaches across. A different
#: judgement from `Coverage`, and deliberately a different type.
#:
#: The chart endpoint returns one of each, side by side. While both were called
#: `coverage` and typed `Coverage`, the obvious line to write --
#: `worst_coverage([chart.coverage, comparison.coverage])` -- ranked a
#: benchmark's span shortfall against a price's staleness and produced a number
#: that means nothing, with nothing in the type system to object. Two names
#: would have made that unlikely; two types make it impossible, which is how
#: this codebase already handles the two close columns (Sec 7.5).
#:
#: `manual` is absent rather than unused. It is a claim about where a price came
#: from, and a span has no such claim to make: span is measured from
#: `TotalReturnPoint`s, which carry no `source`, so `classify()` is never
#: reached. Leaving it in the vocabulary let the screen render a
#: `BENCHMARK SPAN - MANUAL` badge that could never legitimately fire.
SpanCoverage = Literal["missing", "partial", "full"]


class DecimalString(TypeDecorator[Decimal]):
    impl = String
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
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
        return str(value)

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        return value if isinstance(value, Decimal) else Decimal(str(value))

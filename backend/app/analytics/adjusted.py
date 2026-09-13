"""The adjusted close's shared vocabulary. Deliberately holds no query.

Parent doc Sec 7.5 forbids any call path from reaching both closes. That rule is
about what a caller can OBTAIN, and this module lets a caller obtain nothing: it
declares the point type and maps rows onto it, and it cannot fetch a row. Reaching
it is therefore always safe, which is what lets `total_return.py` and
`benchmark_return.py` share a vocabulary without importing each other.

The alternative is worse in both directions. If the benchmark reader imported the
instrument reader for `TotalReturnPoint`, every caller wanting a benchmark would
acquire a call path to an instrument's adjusted closes -- the exact thing M6a
section 6.1 splits the readers to avoid. If each reader declared its own point
type instead, `instrument_return.py` would be comparing two structurally identical
classes that are not the same class, and the first `isinstance` or shared helper
would have to pick one.

This is the adjusted-close analogue of `quotes.py`, and for the same reason:
machinery that is safe for every reader lives apart from the readers that bind a
module to one column. `quotes.py` names neither close and serves both lanes; this
file names one close and serves both readers of it. Do not merge either into a
reader.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TypeVar

from app.models.market import BenchmarkDaily, PriceDaily

#: The two tables that carry a dated adjusted close. Constrained rather than
#: bounded, and rather than a union of sequences: mypy widens
#: `Sequence[PriceDaily] | Sequence[BenchmarkDaily]` to their shared `SQLModel`
#: base and then cannot see any of the three fields. Same shape as
#: `ingest/source_ref.py`'s `_Ref`.
_Row = TypeVar("_Row", PriceDaily, BenchmarkDaily)


@dataclass(frozen=True, slots=True)
class TotalReturnPoint:
    on: date
    close_adjusted: Decimal
    #: The currency the series is quoted in, as the provider reported it --
    #: carried rather than assumed, for the reason parent doc Sec 5.3 gives
    #: about rates: a price without a stated currency is a runtime error
    #: waiting to be plausible.
    currency: str


def points(rows: Sequence[_Row]) -> tuple[TotalReturnPoint, ...]:
    """The same three fields off either table. Chronological, always."""
    return tuple(
        TotalReturnPoint(
            on=row.price_date,
            close_adjusted=row.close_adjusted,
            currency=row.currency,
        )
        for row in sorted(rows, key=lambda row: row.price_date)
    )

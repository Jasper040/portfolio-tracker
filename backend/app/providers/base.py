"""What a price, FX or symbol provider looks like from the inside of the app.

Protocols rather than base classes, and the network confined to this package,
for the same reason `domain/` imports no ORM: the arithmetic has to be testable
without either. Nothing outside `app/providers/` makes an HTTP call.

The value objects here are deliberately thin. A provider's job is to answer one
question and say who answered it; deciding whether the answer is believable is
`domain/symbols.py`, and deciding what a missing answer means is
`analytics/valuation.py`. A provider that made either decision would be an
untestable place for the most important judgement in M2 to live.

`source` travels on every series because M2's coverage values are claims about
provenance. `coverage: "manual"` says a component came from a file somebody
typed, and the only reason that can be a fact rather than an assumption is that
the row records who supplied it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

#: The source string a hand-maintained price carries. Reaching `coverage:
#: "manual"` is a string comparison against this, so it lives in one place.
MANUAL = "manual"

#: The cache depth (M2-7): a fixed five years, one call per instrument, no date
#: arithmetic. The valuation series starts later -- at the first day a position
#: existed -- because cache depth and chart start are separate concerns. The
#: depth is what lets a reader ask to look back five years and be answered.
BACKFILL_YEARS = 5

class ProviderError(RuntimeError):
    """A provider could not be reached or answered with something unusable.

    Distinct from "answered with nothing", which is `None` and is a normal
    result: an instrument may genuinely have no series. An exception here means
    the network or the response shape failed, and `fetch-prices` reports it
    rather than recording an absence that would read as `coverage: "missing"`.
    """

@dataclass(frozen=True, slots=True)
class PricePoint:
    """One day's closes.

    Both are split-adjusted. "Unadjusted" means dividend-unadjusted:
    `close_unadjusted` is the plain close that valuation and the symbol
    discriminator read, `close_adjusted` the total-return series that only
    `analytics/total_return.py` may read (parent doc Sec 7.5).
    """

    on: date
    close_unadjusted: Decimal
    close_adjusted: Decimal

@dataclass(frozen=True, slots=True)
class PriceSeries:
    symbol: str
    #: As the PROVIDER reported it, never assumed from the ledger. A match
    #: between the two is one of the three conditions the discriminator checks.
    currency: str
    source: str
    points: tuple[PricePoint, ...]

@dataclass(frozen=True, slots=True)
class FxPoint:
    on: date
    #: Units of `from_ccy` per 1 unit of `to_ccy`. Divide, never multiply.
    rate: Decimal

@dataclass(frozen=True, slots=True)
class FxSeries:
    from_ccy: str
    to_ccy: str
    source: str
    points: tuple[FxPoint, ...]

@dataclass(frozen=True, slots=True)
class SymbolCandidate:
    """One possible ticker for an ISIN. A candidate, never an answer.

    The provider spike's central finding is that a resolver returning this is
    saying "here is something with that ISIN attached", not "here is the
    instrument you traded". `domain/symbols.py` decides.
    """

    symbol: str
    name: str
    exchange_code: str
    source: str

class SymbolResolver(Protocol):
    name: str

    def candidates(self, isin: str) -> tuple[SymbolCandidate, ...]:
        """Every ticker this resolver associates with `isin`, best guess first."""
        ...

class PriceProvider(Protocol):
    name: str

    def full_series(self, symbol: str) -> PriceSeries | None:
        """The fixed five-year history (M2-7). One call, no date arithmetic."""
        ...

    def series_since(self, symbol: str, since: date) -> PriceSeries | None:
        """Only what the cache lacks (M2-9), from `since` to today inclusive."""
        ...

class FxProvider(Protocol):
    name: str

    def series(self, from_ccy: str, to_ccy: str, *, start: date, end: date) -> FxSeries | None:
        """Daily rates for one pair. `rate` follows `FxDaily`'s direction."""
        ...

def latest_point(points: Sequence[PricePoint]) -> PricePoint | None:
    """The most recent point, or None. Used to decide whether to refetch."""
    return max(points, key=lambda point: point.on) if points else None

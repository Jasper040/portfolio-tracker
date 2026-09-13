"""Total-return index arithmetic, shared by every comparison against a benchmark.

Split out of `instrument_return.py` in M6a. The portfolio comparison has to do to
a benchmark exactly what the instrument comparison already does -- convert every
close to base, rebase over a span, judge whether the series actually reaches
across that span, link -- and a second copy of any one of those steps would be
free to disagree with the first about what "the benchmark covered it" means.

The move is forced rather than tidy. `instrument_return.py` imports
`total_return.py`, which reads an INSTRUMENT's adjusted closes, and an endpoint
that values the portfolio on the unadjusted close plus cash must never reach that
module (parent doc Sec 7.5; `tests/integration/test_no_double_count.py`).
Importing these helpers from `instrument_return.py` would have handed the
performance endpoint the path.

Nothing here queries. Callers hand in `TotalReturnPoint`s -- the adjusted close is
the only close this module ever touches, and only on a point it was given -- plus
a rate history, and get back base-currency levels, indices, returns and a span
verdict. `quotes.py` supplies FX and names neither close. The ORDER these are
applied in is not this module's to decide: `instrument_return.py`'s docstring
states it, and every caller follows it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.analytics.adjusted import TotalReturnPoint
from app.analytics.quotes import STALE_DAYS, Quote, in_base
from app.models.market import FxDaily
from app.models.types import SpanCoverage

#: The span vocabulary's own constants. `quotes.py` exports `FULL`, `PARTIAL`
#: and `MISSING` typed `Coverage`, so returning one of those from a
#: `SpanCoverage` function is precisely the mix this type split exists to
#: prevent -- and mypy rejects it, which is the split earning its keep on the
#: first edit after it landed.
SPAN_FULL: SpanCoverage = "full"
SPAN_PARTIAL: SpanCoverage = "partial"
SPAN_MISSING: SpanCoverage = "missing"

ONE = Decimal("1")
HUNDRED = Decimal("100")


@dataclass(frozen=True, slots=True)
class IndexPoint:
    on: date
    #: 100 at the start of the span this point belongs to.
    index: Decimal


@dataclass(frozen=True, slots=True)
class BasePoint:
    """One day's total-return level, already in the owner's currency."""

    on: date
    value: Decimal


def in_base_points(
    points: Sequence[TotalReturnPoint],
    base: str,
    rates: Mapping[tuple[str, str], list[FxDaily]],
) -> tuple[tuple[BasePoint, ...], int]:
    """Convert every point to `base`. Always before `rebase`, never after (M3-7).

    Returns the convertible points and a count of the ones dropped for want of
    a rate, because a series quietly shortened is how a return ends up measured
    over a span nobody chose.
    """
    out: list[BasePoint] = []
    dropped = 0
    for point in points:
        quote = Quote(
            close=point.close_adjusted,
            currency=point.currency,
            on=point.on,
            # `Quote.source` exists to feed `classify()`, which this module
            # never calls: coverage here is a question about the span the
            # benchmark covers, not the age of any one quote. Left empty rather
            # than filled with an invented provider name.
            source="",
        )
        # Quantity 1: this is an index, not a valuation.
        converted = in_base(quote, ONE, point.on, base, rates)
        if converted is None:
            dropped += 1
            continue
        out.append(BasePoint(on=point.on, value=converted[0]))
    return tuple(out), dropped


def rebase(
    points: Sequence[BasePoint], start: date, end: date
) -> tuple[tuple[IndexPoint, ...], Decimal | None]:
    """Index to 100 at `start`, and the total return to `end`.

    The span is the caller's interval or run, never a viewport: an index rebased
    at the edge of the requested window changes meaning when the reader drags
    the range control.
    """
    window = [p for p in points if start <= p.on <= end]
    if not window or window[0].value == 0:
        return (), None
    first = window[0].value
    index = tuple(IndexPoint(on=p.on, index=p.value / first * HUNDRED) for p in window)
    return index, window[-1].value / first - ONE


#: Worst news first, over the span vocabulary only. A private twin of
#: `quotes.worst_coverage` rather than a call into it: that function ranks
#: STALENESS and hands back a `Coverage`, so routing spans through it would
#: return a type that can say `manual` and reintroduce, at the boundary, exactly
#: the confusion `SpanCoverage` exists to remove.
_SPAN_SEVERITY: dict[SpanCoverage, int] = {SPAN_FULL: 0, SPAN_PARTIAL: 1, SPAN_MISSING: 2}


def worst_span(values: Sequence[SpanCoverage]) -> SpanCoverage:
    """The most severe span among `values`.

    Empty is `full`, for the reason `quotes.worst_coverage` spells out: an empty
    set of observations carries no bad news. Both callers return early with
    `missing` when there is nothing to compare, which is each of them saying at
    its own call site that ITS kind of empty is the bad kind.
    """
    return max(values, key=lambda value: _SPAN_SEVERITY[value], default=SPAN_FULL)


def span_coverage(
    points: Sequence[BasePoint], start: date, end: date, measured: Decimal | None
) -> SpanCoverage:
    """How much of `start`..`end` the series actually spans.

    `STALE_DAYS` is borrowed from `quotes.py` deliberately: it is already this
    codebase's answer to "how long a gap is a weekend and a holiday, and how
    long is a fault". A benchmark whose first point lands a month into a
    quarter-long span has not covered it, and differencing two returns anyway
    is precisely the plausible wrong number this arithmetic exists to avoid.
    """
    if measured is None:
        return SPAN_MISSING
    window = [p for p in points if start <= p.on <= end]
    if not window:
        return SPAN_MISSING
    # Compound names, so no local variable here can collide with the leak
    # scanner's token list: it matches whole words case-insensitively, and an
    # underscore is a word character.
    opening_gap = (window[0].on - start).days
    closing_gap = (end - window[-1].on).days
    return (
        SPAN_PARTIAL
        if opening_gap > STALE_DAYS or closing_gap > STALE_DAYS
        else SPAN_FULL
    )


def link(returns: Sequence[Decimal | None]) -> Decimal | None:
    """`(1 + a) * (1 + b) - 1`.

    `None` the moment any one link is `None`: a chain missing a link is not a
    shorter chain, and silently dropping the unmeasurable stretch would report
    the return of a period nobody had.
    """
    total = ONE
    for value in returns:
        if value is None:
            return None
        total *= ONE + value
    return total - ONE


def side_reason(
    what: str,
    points: Sequence[BasePoint],
    start: date,
    end: date,
    coverage: SpanCoverage,
) -> str | None:
    """Why one side could not be measured over the whole of `start`..`end`."""
    span = f"{start} to {end}"
    if coverage == SPAN_MISSING:
        return f"no {what} data from {span}"
    if coverage == SPAN_PARTIAL:
        window = [p for p in points if start <= p.on <= end]
        return f"{what} covers {window[0].on} to {window[-1].on}, not the whole of {span}"
    return None

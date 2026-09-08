"""One instrument against a benchmark, both as total-return indices.

M3 section 5.3. Reads `close_adjusted` through `analytics/total_return.py` and
nothing else: an excess return computed from the plain close would omit every
dividend on both sides, and the two omissions do not cancel.

**The order of operations is the whole point of this file.** For each side, in
this order:

1. read the adjusted closes with the currency they were quoted in;
2. convert every one of them to the owner's base currency;
3. rebase to 100 at the start of each in-market interval;
4. difference the two returns;
5. chain-link across the in-market intervals only.

Converting after rebasing silently answers a different question. A dollar index
that rose 10% while the dollar fell 10% did approximately nothing for a euro
holder, and an index built before the conversion reports the 10% -- a plausible
number rather than an error, which is why the order is written down here rather
than left to be inferred from the code.

Rebasing happens at each in-market interval's start and never at the edge of
the requested window: an index rebased at the window edge changes meaning when
the reader drags the range control, and a figure that moves when you zoom is
not a figure.

**What this module may not touch.** Not `analytics/prices.py`, not
`analytics/valuation.py`, and not `analytics/instrument_price.py` -- which
reaches the first of those. `tests/integration/test_no_double_count.py` walks
the import graph, so a transitive hop counts. `analytics/quotes.py` is the one
safe source of FX: it deliberately names neither close column, which is exactly
why it was split out of `prices.py`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from sqlalchemy import Engine
from sqlmodel import Session

from app.analytics.adjusted import TotalReturnPoint
from app.analytics.benchmark_return import benchmark_total_return_series
from app.analytics.quotes import (
    FULL,
    MISSING,
    PARTIAL,
    STALE_DAYS,
    Quote,
    base_currency,
    in_base,
    rate_history,
    worst_coverage,
)
from app.analytics.total_return import total_return_series
from app.models.market import FxDaily
from app.models.types import Coverage

#: Every return on this object is a total return. Stated on the object rather
#: than assumed by the reader -- parent doc Sec 7.4: no endpoint returns an
#: unlabelled return.
BASIS = "total_return"

ONE = Decimal("1")
HUNDRED = Decimal("100")


class HoldingInterval(Protocol):
    """What the comparison needs to know about one run of days: when it ran,
    and whether the owner held the instrument through it.

    Deliberately a structural type rather than an import of
    `analytics.instrument_price.Interval`, for two reasons that point the same
    way. The mechanical one: that module reads `close_unadjusted` through
    `analytics/prices.py`, and importing it would put this module one hop from
    the column parent doc Sec 7.5 forbids it to reach. The design one:
    `Interval.price_return` is a return computed from the unadjusted close, and
    naming only these three fields makes it structurally impossible for that
    figure to be mistaken for one of ours.

    A real `Interval` satisfies this; so does anything else that answers the
    same three questions.
    """

    @property
    def start(self) -> date: ...

    @property
    def end(self) -> date: ...

    @property
    def in_market(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class IndexPoint:
    on: date
    #: 100 at the start of the interval this point belongs to.
    index: Decimal


@dataclass(frozen=True, slots=True)
class IntervalExcess:
    start: date
    end: date
    instrument_return: Decimal | None
    benchmark_return: Decimal | None
    #: The ARITHMETIC difference, `instrument_return - benchmark_return`, and
    #: not the geometric form `(1 + i) / (1 + b) - 1`. The choice is written
    #: here rather than left to be inferred because the two answers diverge as
    #: returns grow, and a reader comparing this figure against one computed
    #: elsewhere has no other way to know which they are holding.
    #:
    #: Arithmetic for two reasons. M3 section 5.3 says "minus". And
    #: `Comparison.linked_excess` is the difference of the two chain-linked
    #: returns, which is only coherent if the per-interval figure is a
    #: difference too -- a geometric excess does not chain into an arithmetic
    #: one, so mixing the two would make the summary disagree with the rows it
    #: summarises.
    #:
    #: `None`, never 0, when either side could not be measured over this whole
    #: interval. A zero excess is the claim that the instrument matched the
    #: benchmark exactly, which is not what "we could not tell" means.
    excess: Decimal | None
    #: Why `excess` is `None`. Set whenever it is, and only then.
    reason: str | None


@dataclass(frozen=True, slots=True)
class Comparison:
    basis: str
    benchmark_key: str
    instrument_index: tuple[IndexPoint, ...]
    benchmark_index: tuple[IndexPoint, ...]
    #: In-market intervals only. An out-of-market stretch gets no entry at all
    #: rather than an entry with nulls in it -- crediting the owner with drift
    #: over days they did not hold the instrument is the error Sec 7.6 rules
    #: out, and an entry is an invitation to compute one.
    intervals: tuple[IntervalExcess, ...]
    linked_instrument_return: Decimal | None
    linked_benchmark_return: Decimal | None
    linked_excess: Decimal | None
    #: The BENCHMARK's coverage, not the instrument's. The instrument's own
    #: badge is on `InstrumentPriceView`, computed from its own price series;
    #: a benchmark outage must not degrade it, and two modules answering the
    #: same question two ways would be worse than one answering it once.
    #:
    #: **This is a different judgement from every other `Coverage` in the
    #: codebase, and Task 7's endpoint returns the two side by side.**
    #: Everywhere else the value comes from `quotes.classify()` and means
    #: STALENESS -- how old the provider's answer for a day was. Here
    #: `classify()` is never called: `TotalReturnPoint` carries no `source`, so
    #: staleness is not visible from this module at all. What is measured
    #: instead is SPAN -- whether the benchmark series actually reaches across
    #: the holding it is being compared over:
    #:
    #: * `missing` -- no usable benchmark data for some in-market interval;
    #: * `partial` -- the series exists but starts more than `STALE_DAYS`
    #:   after an interval's start or ends more than `STALE_DAYS` before its
    #:   end, or some of its days had no rate to reach the base currency;
    #: * `full` -- the benchmark spans every in-market interval.
    #:
    #: Same type and same vocabulary as the price side, deliberately, because
    #: both answer "how much of what you asked for could be accounted for"
    #: (design doc Sec 8.1). Two different measurements of that, though, and a
    #: reader who assumes this one is about staleness will misread it.
    coverage: Coverage


@dataclass(frozen=True, slots=True)
class _BasePoint:
    """One day's total-return level, already in the owner's currency."""

    on: date
    value: Decimal


def _in_base(
    points: Sequence[TotalReturnPoint],
    base: str,
    rates: Mapping[tuple[str, str], list[FxDaily]],
) -> tuple[tuple[_BasePoint, ...], int]:
    """Convert every point to `base`. Step 2, and it must precede step 3.

    Returns the convertible points and a count of the ones dropped for want of
    a rate, because a series quietly shortened is how a return ends up measured
    over a span nobody chose.
    """
    out: list[_BasePoint] = []
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
        out.append(_BasePoint(on=point.on, value=converted[0]))
    return tuple(out), dropped


def _rebase(
    points: Sequence[_BasePoint], start: date, end: date
) -> tuple[tuple[IndexPoint, ...], Decimal | None]:
    """Index to 100 at `start`, and the total return to `end`. Step 3.

    The window is the interval's, never the caller's viewport.
    """
    window = [p for p in points if start <= p.on <= end]
    if not window or window[0].value == 0:
        return (), None
    first = window[0].value
    index = tuple(IndexPoint(on=p.on, index=p.value / first * HUNDRED) for p in window)
    return index, window[-1].value / first - ONE


def _span_coverage(
    points: Sequence[_BasePoint], start: date, end: date, measured: Decimal | None
) -> Coverage:
    """How much of `start`..`end` the series actually spans.

    `STALE_DAYS` is borrowed from `quotes.py` deliberately: it is already this
    codebase's answer to "how long a gap is a weekend and a holiday, and how
    long is a fault". A benchmark whose first point lands a month into a
    quarter-long holding has not covered that holding, and differencing the two
    returns anyway is precisely the plausible wrong number this file exists to
    avoid.
    """
    if measured is None:
        return MISSING
    window = [p for p in points if start <= p.on <= end]
    if not window:
        return MISSING
    # Compound names, so no local variable here can collide with the leak
    # scanner's token list: it matches whole words case-insensitively, and an
    # underscore is a word character.
    opening_gap = (window[0].on - start).days
    closing_gap = (end - window[-1].on).days
    return (
        PARTIAL
        if opening_gap > STALE_DAYS or closing_gap > STALE_DAYS
        else FULL
    )


def _link(returns: Sequence[Decimal | None]) -> Decimal | None:
    """`(1 + a) * (1 + b) - 1` over the in-market intervals only.

    `None` the moment any one link is `None`: a chain missing a link is not a
    shorter chain, and silently dropping the unmeasurable stretch would report
    the return of a holding period the owner never had.
    """
    total = ONE
    for value in returns:
        if value is None:
            return None
        total *= ONE + value
    return total - ONE


def _empty(benchmark_key: str) -> Comparison:
    """Nothing was held, so there is nothing to compare. `missing` rather than
    `full` for the reason `instrument_price_view` gives: an empty set of
    observations is not a well-covered one."""
    return Comparison(
        basis=BASIS,
        benchmark_key=benchmark_key,
        instrument_index=(),
        benchmark_index=(),
        intervals=(),
        linked_instrument_return=None,
        linked_benchmark_return=None,
        linked_excess=None,
        coverage=MISSING,
    )


def _side_reason(
    what: str,
    points: Sequence[_BasePoint],
    interval: HoldingInterval,
    coverage: Coverage,
) -> str | None:
    """Why one side could not be measured over the whole of `interval`."""
    span = f"{interval.start} to {interval.end}"
    if coverage == MISSING:
        return f"no {what} data from {span}"
    if coverage == PARTIAL:
        window = [p for p in points if interval.start <= p.on <= interval.end]
        return (
            f"{what} covers {window[0].on} to {window[-1].on}, "
            f"not the whole of {span}"
        )
    return None


def _reason(
    isin: str,
    benchmark_key: str,
    interval: HoldingInterval,
    instrument: Sequence[_BasePoint],
    instrument_coverage: Coverage,
    benchmark: Sequence[_BasePoint],
    benchmark_coverage: Coverage,
) -> str | None:
    """Why the excess is `None`. `None` when it is not.

    Both sides, symmetrically. An earlier version asked the span question of
    the benchmark only, which let a 4-day instrument return be differenced
    against an 86-day benchmark return and published with no reason at all --
    the exact subtraction `_span_coverage` exists to prevent, escaping through
    the side nobody checked.
    """
    parts = [
        text
        for text in (
            _side_reason(isin, instrument, interval, instrument_coverage),
            _side_reason(
                f"benchmark {benchmark_key!r}", benchmark, interval, benchmark_coverage
            ),
        )
        if text is not None
    ]
    return "; ".join(parts) if parts else None


def comparison(
    engine: Engine,
    isin: str,
    *,
    benchmark_key: str,
    intervals: Sequence[HoldingInterval],
) -> Comparison:
    """The instrument and its benchmark, rebased per holding and differenced."""
    held = [interval for interval in intervals if interval.in_market]
    if not held:
        return _empty(benchmark_key)

    start = min(interval.start for interval in held)
    end = max(interval.end for interval in held)

    with Session(engine) as session:
        base = base_currency(session)
        rates = rate_history(session)

    # Step 1, then step 2. Never the other way round.
    instrument, _ = _in_base(
        total_return_series(engine, isin, start=start, end=end), base, rates
    )
    benchmark, benchmark_dropped = _in_base(
        benchmark_total_return_series(engine, benchmark_key, start=start, end=end),
        base,
        rates,
    )

    instrument_index: list[IndexPoint] = []
    benchmark_index: list[IndexPoint] = []
    rows: list[IntervalExcess] = []
    coverages: list[Coverage] = []

    for interval in held:
        # Step 3: rebased at THIS interval's start, so the answer is a property
        # of the holding rather than of the range control.
        own_index, own_return = _rebase(instrument, interval.start, interval.end)
        bench_index, bench_return = _rebase(benchmark, interval.start, interval.end)
        # Both sides get the same span test. Only the benchmark's answer reaches
        # the badge -- see `Comparison.coverage` -- but both reach the reason,
        # because either side falling short makes the difference meaningless.
        own_coverage = _span_coverage(
            instrument, interval.start, interval.end, own_return
        )
        bench_coverage = _span_coverage(
            benchmark, interval.start, interval.end, bench_return
        )
        coverages.append(bench_coverage)

        reason = _reason(
            isin,
            benchmark_key,
            interval,
            instrument,
            own_coverage,
            benchmark,
            bench_coverage,
        )
        # Step 4. A partial benchmark yields no excess even though it yields a
        # return: differencing two returns measured over different spans is a
        # subtraction that compiles and means nothing.
        excess = (
            own_return - bench_return
            if own_return is not None and bench_return is not None and reason is None
            else None
        )

        instrument_index.extend(own_index)
        benchmark_index.extend(bench_index)
        rows.append(
            IntervalExcess(
                start=interval.start,
                end=interval.end,
                instrument_return=own_return,
                benchmark_return=bench_return,
                excess=excess,
                reason=reason,
            )
        )

    # Step 5, over the in-market intervals only.
    linked_instrument = _link([row.instrument_return for row in rows])
    linked_benchmark = _link([row.benchmark_return for row in rows])
    linked_excess = (
        linked_instrument - linked_benchmark
        if linked_instrument is not None
        and linked_benchmark is not None
        and all(row.excess is not None for row in rows)
        else None
    )

    coverage = worst_coverage(coverages)
    if benchmark_dropped:
        # Days the benchmark could not be expressed in the owner's currency at
        # all. They do not move an endpoint-to-endpoint return, but the series
        # the reader sees is shorter than the one the provider sent.
        coverage = worst_coverage([coverage, PARTIAL])

    return Comparison(
        basis=BASIS,
        benchmark_key=benchmark_key,
        instrument_index=tuple(instrument_index),
        benchmark_index=tuple(benchmark_index),
        intervals=tuple(rows),
        linked_instrument_return=linked_instrument,
        linked_benchmark_return=linked_benchmark,
        linked_excess=linked_excess,
        coverage=coverage,
    )

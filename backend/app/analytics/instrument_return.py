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

**What moved out.** The arithmetic behind steps 2, 3 and 5, and the span
judgement, live in `indexing.py` since M6a, so the portfolio comparison applies
the same rules without reaching `total_return.py` through this file. The order
above is still this module's to state and to follow.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from sqlalchemy import Engine
from sqlmodel import Session

from app.analytics.benchmark_return import benchmark_total_return_series
from app.analytics.indexing import (
    SPAN_MISSING,
    SPAN_PARTIAL,
    BasePoint,
    IndexPoint,
    in_base_points,
    link,
    rebase,
    side_reason,
    span_coverage,
    worst_span,
)
from app.analytics.quotes import base_currency, rate_history
from app.analytics.total_return import total_return_series
from app.models.types import SpanCoverage

#: Every return on this object is a total return. Stated on the object rather
#: than assumed by the reader -- parent doc Sec 7.4: no endpoint returns an
#: unlabelled return.
BASIS = "total_return"


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
    #: A DIFFERENT type from the price side's `Coverage`, and named for what it
    #: measures rather than for the family it belongs to. Both answer "how much
    #: of what you asked for could be accounted for" (design doc Sec 8.1), but
    #: they answer it about different things -- and while they shared a name and
    #: a type, `worst_coverage([chart.coverage, comparison.coverage])` was the
    #: obvious line to write, typechecked, and meant nothing. `SpanCoverage`
    #: also drops `manual`, which was unreachable here; see the type's own
    #: comment in `models/types.py`.
    span: SpanCoverage


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
        span=SPAN_MISSING,
    )


def _reason(
    isin: str,
    benchmark_key: str,
    interval: HoldingInterval,
    instrument: Sequence[BasePoint],
    instrument_coverage: SpanCoverage,
    benchmark: Sequence[BasePoint],
    benchmark_coverage: SpanCoverage,
) -> str | None:
    """Why the excess is `None`. `None` when it is not.

    Both sides, symmetrically. An earlier version asked the span question of
    the benchmark only, which let a 4-day instrument return be differenced
    against an 86-day benchmark return and published with no reason at all --
    the exact subtraction `span_coverage` exists to prevent, escaping through
    the side nobody checked.
    """
    parts = [
        text
        for text in (
            side_reason(
                isin, instrument, interval.start, interval.end, instrument_coverage
            ),
            side_reason(
                f"benchmark {benchmark_key!r}",
                benchmark,
                interval.start,
                interval.end,
                benchmark_coverage,
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
    # Sorted, not merely filtered. `start`/`end` below use min/max, so today's
    # only caller gets the same arithmetic either way -- but the per-interval
    # rows and both index series come out in list order, so an unsorted argument
    # would silently produce a chart drawn out of sequence. An unstated
    # precondition on a public function is a defect waiting for its second
    # caller.
    held = sorted(
        (interval for interval in intervals if interval.in_market),
        key=lambda interval: (interval.start, interval.end),
    )
    if not held:
        return _empty(benchmark_key)

    start = min(interval.start for interval in held)
    end = max(interval.end for interval in held)

    with Session(engine) as session:
        base = base_currency(session)
        rates = rate_history(session)

    # Step 1, then step 2. Never the other way round.
    instrument, _ = in_base_points(
        total_return_series(engine, isin, start=start, end=end), base, rates
    )
    benchmark, benchmark_dropped = in_base_points(
        benchmark_total_return_series(engine, benchmark_key, start=start, end=end),
        base,
        rates,
    )

    instrument_index: list[IndexPoint] = []
    benchmark_index: list[IndexPoint] = []
    rows: list[IntervalExcess] = []
    spans: list[SpanCoverage] = []

    for interval in held:
        # Step 3: rebased at THIS interval's start, so the answer is a property
        # of the holding rather than of the range control.
        own_index, own_return = rebase(instrument, interval.start, interval.end)
        bench_index, bench_return = rebase(benchmark, interval.start, interval.end)
        # Both sides get the same span test. Only the benchmark's answer reaches
        # the badge -- see `Comparison.span` -- but both reach the reason,
        # because either side falling short makes the difference meaningless.
        own_coverage = span_coverage(
            instrument, interval.start, interval.end, own_return
        )
        bench_coverage = span_coverage(
            benchmark, interval.start, interval.end, bench_return
        )
        spans.append(bench_coverage)

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
    linked_instrument = link([row.instrument_return for row in rows])
    linked_benchmark = link([row.benchmark_return for row in rows])
    linked_excess = (
        linked_instrument - linked_benchmark
        if linked_instrument is not None
        and linked_benchmark is not None
        and all(row.excess is not None for row in rows)
        else None
    )

    span = worst_span(spans)
    if benchmark_dropped:
        # Days the benchmark could not be expressed in the owner's currency at
        # all. They do not move an endpoint-to-endpoint return, but the series
        # the reader sees is shorter than the one the provider sent.
        span = worst_span([span, SPAN_PARTIAL])

    return Comparison(
        basis=BASIS,
        benchmark_key=benchmark_key,
        instrument_index=tuple(instrument_index),
        benchmark_index=tuple(benchmark_index),
        intervals=tuple(rows),
        linked_instrument_return=linked_instrument,
        linked_benchmark_return=linked_benchmark,
        linked_excess=linked_excess,
        span=span,
    )

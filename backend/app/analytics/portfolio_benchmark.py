"""The portfolio's time-weighted return against one benchmark, labelled on both sides.

M6a section 7. Measures a benchmark over each of `portfolio_return.py`'s runs,
with the same conversion, rebasing and span rules the instrument comparison uses
-- `indexing.py` holds them once for both.

**Why a benchmark is safe beside a valuation path when an instrument is not.** A
benchmark is never held, pays the owner nothing and appears in no cash balance,
so there is nothing to count twice (M6a section 6.1). This module reaches
`benchmark_return.py` and never `total_return.py`;
`tests/integration/test_no_double_count.py` asserts both.

**Per run, never across a gap.** Each run is compared over exactly its own span.
The window's figures exist only when the portfolio's own window figure does: an
excess spanning a gap would be M6a-7's forbidden figure with a benchmark
subtracted from it.

**The two sides differ, and both labels say how** (M6a-10). The portfolio's
dividends land in cash net of withholding and sit there; the benchmark's adjusted
close reinvests them gross. Both differences favour the benchmark. Neither is
engineered away -- a figure that silently folded withholding tax and
reinvestment convention into "you underperformed" would be the unlabelled return
parent doc Sec 7.4 forbids.

Excess is the ARITHMETIC difference, `portfolio - benchmark`, for the reasons
`instrument_return.IntervalExcess.excess` gives.

Runs arrive structurally through `MeasuredRun`, for the reason
`instrument_return.HoldingInterval` gives: importing `portfolio_return` would
put this module one hop from `valuation.py` and `prices.py`, and it needs
nothing from either.
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
    IndexPoint,
    in_base_points,
    rebase,
    side_reason,
    span_coverage,
    worst_span,
)
from app.analytics.quotes import base_currency, rate_history
from app.models.types import SpanCoverage

#: The benchmark side's construction: adjusted closes, dividends included.
BASIS = "total_return"
#: How dividends reach the benchmark's figure: reinvested on the ex-date, gross.
DIVIDENDS = "reinvested_gross"


class MeasuredRun(Protocol):
    """What the comparison needs from a run: its span and its one figure."""

    @property
    def start(self) -> date: ...

    @property
    def end(self) -> date: ...

    @property
    def linked_return(self) -> Decimal: ...


@dataclass(frozen=True, slots=True)
class RunExcess:
    start: date
    end: date
    portfolio_return: Decimal
    benchmark_return: Decimal | None
    #: `None`, never 0, when the benchmark does not span the run.
    excess: Decimal | None
    span: SpanCoverage
    #: Why `excess` is `None`. Set exactly when it is.
    reason: str | None


@dataclass(frozen=True, slots=True)
class PortfolioComparison:
    #: The BENCHMARK side's labels. The portfolio's are on `PortfolioReturn`.
    basis: str
    dividends: str
    benchmark_key: str
    #: 100 at each run's start, like `instrument_return.Comparison.benchmark_index`.
    benchmark_index: tuple[IndexPoint, ...]
    runs: tuple[RunExcess, ...]
    #: The window's figures: `None` unless the portfolio's own window figure exists.
    benchmark_return: Decimal | None
    excess: Decimal | None
    #: The benchmark's span over every run -- a span, not the portfolio's
    #: staleness; see `models/types.py` for why the two are different types.
    span: SpanCoverage


def compare_to_benchmark(
    engine: Engine,
    runs: Sequence[MeasuredRun],
    *,
    window_return: Decimal | None,
    benchmark_key: str,
) -> PortfolioComparison:
    """Every run against the benchmark over the same span, and the window when
    the portfolio has a window figure of its own."""
    ordered = sorted(runs, key=lambda run: (run.start, run.end))
    if not ordered:
        return _empty(benchmark_key)

    with Session(engine) as session:
        base = base_currency(session)
        rates = rate_history(session)

    # Convert, then rebase. Never the other way round (M3-7).
    series, dropped = in_base_points(
        benchmark_total_return_series(
            engine,
            benchmark_key,
            start=ordered[0].start,
            end=max(run.end for run in ordered),
        ),
        base,
        rates,
    )

    what = f"benchmark {benchmark_key!r}"
    rows: list[RunExcess] = []
    index: list[IndexPoint] = []
    for run in ordered:
        run_index, measured = rebase(series, run.start, run.end)
        span = span_coverage(series, run.start, run.end, measured)
        reason = side_reason(what, series, run.start, run.end, span)
        index.extend(run_index)
        rows.append(
            RunExcess(
                start=run.start,
                end=run.end,
                portfolio_return=run.linked_return,
                benchmark_return=measured,
                excess=(
                    run.linked_return - measured
                    if measured is not None and reason is None
                    else None
                ),
                span=span,
                reason=reason,
            )
        )

    overall = worst_span([row.span for row in rows])
    if dropped:
        # Days the benchmark could not be expressed in the base currency at all:
        # the drawn series is shorter than the one the provider sent.
        overall = worst_span([overall, SPAN_PARTIAL])

    whole = rows[0] if window_return is not None and len(rows) == 1 else None
    return PortfolioComparison(
        basis=BASIS,
        dividends=DIVIDENDS,
        benchmark_key=benchmark_key,
        benchmark_index=tuple(index),
        runs=tuple(rows),
        benchmark_return=whole.benchmark_return if whole is not None else None,
        excess=whole.excess if whole is not None else None,
        span=overall,
    )


def _empty(benchmark_key: str) -> PortfolioComparison:
    """No run, so nothing to compare. `missing` rather than `full`: an empty set
    of observations is not a well-covered one."""
    return PortfolioComparison(
        basis=BASIS,
        dividends=DIVIDENDS,
        benchmark_key=benchmark_key,
        benchmark_index=(),
        runs=(),
        benchmark_return=None,
        excess=None,
        span=SPAN_MISSING,
    )

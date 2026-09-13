"""The portfolio's time-weighted return, optionally against one benchmark (M6a).

Composes four analytics calls and computes nothing of its own:
`valuation.value_series` and `flows.external_flows` feed
`portfolio_return.portfolio_returns`, and `portfolio_benchmark.compare_to_benchmark`
measures a benchmark over the runs that produces.

**The first endpoint that legitimately reaches both lanes** (M6a section 5).
Valuation reads the unadjusted close; the comparison reads a benchmark's adjusted
close. That is safe for a benchmark and would not be for a holding -- a benchmark
appears in no cash balance -- so this route reaches `benchmark_return.py` and
cannot reach `total_return.py`. `test_no_double_count.py` guards it because that
file DISCOVERS routes (PT-28); nothing had to remember to add it, and it is not
exempt. `get_benchmarks` comes from `api/dependencies.py` for the same reason:
importing it from `routes_instrument.py` would have opened the forbidden path.

**The default window starts at the ledger's first day**, not valuation's M2-7
default of the first position. A return is measured from the first close with
capital in it (M6a section 3.4), and the day money first buys shares earns or
loses the gap between its price and the close; starting at that day's close
would drop it. `date.min` asks `value_series` for everything the ledger has, and
`clamped` is reported only when the CALLER supplied `from` -- a sentinel this
route chose is not a request the reader made.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Engine

from app.analytics.flows import external_flows
from app.analytics.portfolio_benchmark import PortfolioComparison, compare_to_benchmark
from app.analytics.portfolio_return import PortfolioReturn, portfolio_returns
from app.analytics.valuation import value_series
from app.api.dependencies import get_benchmarks
from app.api.routes_transactions import get_engine
from app.api.schemas import IndexPointOut
from app.api.schemas_performance import (
    PerformanceOut,
    PortfolioComparisonOut,
    ReturnLinkOut,
    ReturnRunOut,
    RunExcessOut,
)
from app.ingest.benchmarks import Benchmark

router = APIRouter(prefix="/api", tags=["performance"])


@router.get("/performance", response_model=PerformanceOut)
def get_performance(
    engine: Engine = Depends(get_engine),
    benchmarks: tuple[Benchmark, ...] = Depends(get_benchmarks),
    # `from` is a Python keyword; aliased rather than renamed, as on /api/valuation.
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = Query(default=None),
    benchmark: str | None = Query(default=None),
) -> PerformanceOut:
    if from_ is not None and to is not None and from_ > to:
        raise HTTPException(status_code=422, detail=f"from {from_} is after to {to}")
    configured = sorted(b.key for b in benchmarks)
    if benchmark is not None and benchmark not in configured:
        raise HTTPException(
            status_code=422,
            detail=f"unknown benchmark {benchmark!r}; configured benchmarks are {configured}",
        )

    series = value_series(engine, start=from_ if from_ is not None else date.min, end=to)
    result = portfolio_returns(series.points, external_flows(engine))
    comparison = (
        compare_to_benchmark(
            engine, result.runs, window_return=result.linked_return, benchmark_key=benchmark
        )
        if benchmark is not None
        else None
    )
    return _performance_out(
        result,
        comparison,
        base_currency=series.base_currency,
        requested_from=from_,
        clamped=from_ is not None and series.clamped,
    )


def _performance_out(
    result: PortfolioReturn,
    comparison: PortfolioComparison | None,
    *,
    base_currency: str,
    requested_from: date | None,
    clamped: bool,
) -> PerformanceOut:
    return PerformanceOut(
        basis=result.basis,
        lane=result.lane,
        dividends=result.dividends,
        base_currency=base_currency,
        start=result.start,
        end=result.end,
        requested_from=requested_from,
        clamped=clamped,
        links=[
            ReturnLinkOut(
                date=step.on, since=step.since, flow_base=step.flow_base,
                daily_return=step.daily_return, coverage=step.coverage, reason=step.reason,
            )
            for step in result.links
        ],
        runs=[
            ReturnRunOut(
                start=run.start, end=run.end, days=len(run.links),
                linked_return=run.linked_return, coverage=run.coverage,
            )
            for run in result.runs
        ],
        portfolio_index=[
            IndexPointOut(date=point.on, index=point.index)
            for run in result.runs
            for point in run.index
        ],
        linked_return=result.linked_return,
        gaps=result.gaps,
        reason=result.reason,
        comparison=_comparison_out(comparison) if comparison is not None else None,
        method=None,
        coverage=result.coverage,
    )


def _comparison_out(comparison: PortfolioComparison) -> PortfolioComparisonOut:
    return PortfolioComparisonOut(
        basis=comparison.basis,
        dividends=comparison.dividends,
        benchmark_key=comparison.benchmark_key,
        benchmark_index=[
            IndexPointOut(date=point.on, index=point.index)
            for point in comparison.benchmark_index
        ],
        runs=[
            RunExcessOut(
                start=row.start, end=row.end, portfolio_return=row.portfolio_return,
                benchmark_return=row.benchmark_return, excess=row.excess,
                span=row.span, reason=row.reason,
            )
            for row in comparison.runs
        ],
        benchmark_return=comparison.benchmark_return,
        excess=comparison.excess,
        span=comparison.span,
    )

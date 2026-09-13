"""The instrument chart: one instrument's priced line, plus an optional
benchmark comparison (M3 Task 7).

This route composes `analytics/instrument_price.py` (Sec 5.2, the price side)
and `analytics/instrument_return.py` (Sec 5.3, the comparison side) and
computes nothing of its own -- `test_no_double_count.py`'s
`test_the_instrument_route_reaches_both_modules_but_names_neither_column`
checks that by name. Reading `close_unadjusted` or `close_adjusted` directly
here would give this route a second, competing way to answer a question the
two analytics modules already answer once each.

The two modules deliberately do not know about each other (see both of their
module docstrings), so this file is the one place allowed to reach both: it
passes `instrument_price_view(...)`'s own `Interval` tuple straight into
`comparison(...)`, which accepts it structurally through `HoldingInterval`
without importing the concrete type.

**The window is applied to the POINTS and never to the INTERVALS.** Both
analytics modules are correct in isolation and neither knows what a "range
control" is: `_intervals()` derives its boundaries from whatever points it is
handed, and `comparison()` faithfully rebases at whatever interval start it is
told. Composing them by handing `instrument_price_view` the requested window
made the two correct halves add up to a wrong number -- an in-market run that
predates the window came back truncated to the window edge, so the excess for
a 900-day holding moved from 40% to 307% purely by dragging the range from
`1Y` to `max`, shipped with `coverage: full` and `reason: null`. M3 section
5.3: "an index rebased at the window edge changes meaning when the reader
drags the range control, and a figure that moves when you zoom is not a
figure." So this route asks for the instrument's FULL history, anchored on
`_earliest_trade_date`, and clips only what is drawn. An interval's `start` is
therefore always a real entry date, and every excess figure is a property of
the holding rather than of the viewport.

That clipping is the one thing this route computes, and it is a filter over
dates rather than a second answer to a priced question -- the coverage badge
for the clipped points is rolled up with `quotes.worst_coverage`, the same
single implementation `instrument_price_view` uses, never a second one.

`GET /api/instruments` (M3 Task 9's fix round) also lives here: the list of
every ISIN this ledger has ever traded, for the frontend's instrument picker.
It reads `Transaction` directly rather than `/api/positions`'s snapshot on
purpose -- a fully exited instrument has no open position, and is exactly the
"out of market" case this milestone exists to make visible, not one to hide
from the picker that opens its own chart.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.analytics.instrument_price import instrument_price_view
from app.analytics.instrument_return import HoldingInterval
from app.analytics.instrument_return import comparison as compute_comparison
from app.analytics.quotes import MISSING, worst_coverage
from app.api.routes_transactions import get_engine
from app.api.schemas import (
    BenchmarkListOut,
    BenchmarkOut,
    ComparisonOut,
    IndexPointOut,
    InstrumentChartOut,
    InstrumentListOut,
    InstrumentSummaryOut,
    IntervalExcessOut,
    IntervalOut,
    MarkerOut,
    PricePointOut,
)
from app.ingest.benchmarks import Benchmark
from app.models.ledger import Transaction
from app.models.market import PriceDaily

router = APIRouter(prefix="/api", tags=["instrument"])

#: What the `range` query parameter accepts. Validated by FastAPI/pydantic off
#: this Literal, the same way `LotMethod` validates `/api/positions?method=`.
ChartRange = Literal["1Y", "3Y", "5Y", "max"]

#: Calendar days to look back for each fixed range. `max` is handled
#: separately -- see `_window`.
_RANGE_DAYS: dict[str, int] = {"1Y": 365, "3Y": 365 * 3, "5Y": 365 * 5}


def get_benchmarks(request: Request) -> tuple[Benchmark, ...]:
    """Wired onto `app.state` at construction time -- the same seam as
    `get_engine`, and for the same reason: production wiring keeps a seam
    tests can use instead of FastAPI's `dependency_overrides`."""
    benchmarks: tuple[Benchmark, ...] = request.app.state.benchmarks
    return benchmarks


def _earliest_trade_date(engine: Engine, isin: str) -> date | None:
    """The first day this ISIN was ever traded, or `None` if it never was.

    Doubles as the existence check (an ISIN the ledger never traded is
    "unknown" for this endpoint) and as the anchor for `range=max`: the
    instrument's own inception, not an arbitrary constant.
    """
    with Session(engine) as session:
        row = session.exec(
            select(Transaction)
            .where(Transaction.isin == isin)
            .order_by(Transaction.trade_date)  # type: ignore[arg-type]
            .limit(1)
        ).first()
    return row.trade_date if row is not None else None


def _traded_instruments(engine: Engine) -> list[InstrumentSummaryOut]:
    """Every ISIN this ledger has ever recorded an economic trade for, each
    paired with a display name, ordered by ISIN so the picker's list is
    stable across requests regardless of the order rows happen to come back
    from the database.

    `is_economic` is the same filter `_markers` applies in
    `analytics/instrument_price.py`, and for the same reason: a corporate
    action's legs are recorded as a buy and a sell that never happened, and
    without this clause they would conjure a phantom instrument nobody ever
    traded.

    Deduplicated in Python rather than with a `DISTINCT ON` (Postgres-only,
    and this project also runs on SQLite in tests): rows are read in trade-date
    order, so the LAST economic trade's `product_name` wins for an ISIN whose
    name ever changed on the wire, and this is small, ledger-scale data --
    a personal portfolio's transaction count, not a warehouse table.
    """
    with Session(engine) as session:
        rows = session.exec(
            select(Transaction)
            .where(Transaction.is_economic == True)  # noqa: E712 -- SQL, not Python
            .order_by(
                Transaction.trade_date,  # type: ignore[arg-type]
                Transaction.source_ref,
                Transaction.id,  # type: ignore[arg-type]
            )
        ).all()

    names: dict[str, str] = {}
    for row in rows:
        if row.isin is None:
            continue
        names[row.isin] = row.product_name or row.isin

    return [
        InstrumentSummaryOut(isin=isin, product_name=name)
        for isin, name in sorted(names.items())
    ]


def _latest_priced_day(engine: Engine, isin: str) -> date | None:
    """The most recent day this ISIN actually has a price for -- the anchor
    for the right edge of every range, `None` if it has never been priced.

    Not `date.today()`. `analytics/valuation.py`'s `window_end = end or
    cash_rows[-1].cash_date` anchors on the newest row actually in hand, never
    on the wall clock, and `routes_valuation.py` adds no wall-clock fallback of
    its own -- this route follows the same convention one level down, at the
    instrument rather than the ledger. A price fetch that lags "today" by a
    weekend, a holiday, or simply an un-run job would otherwise leave the
    window's trailing days `close_base: None` for the whole lag, and the chart
    would end in a gap that looks broken instead of ending on the last day it
    actually has data for.
    """
    with Session(engine) as session:
        row = session.exec(
            select(PriceDaily)
            .where(PriceDaily.isin == isin)
            .order_by(PriceDaily.price_date.desc())  # type: ignore[attr-defined]
            .limit(1)
        ).first()
    return row.price_date if row is not None else None


def _window(chart_range: str, earliest: date, anchor: date) -> tuple[date, date]:
    """What the caller ASKED for, before any clamping. `max` is the whole
    history by definition, so it asks for exactly the inception date."""
    if chart_range == "max":
        return earliest, anchor
    return anchor - timedelta(days=_RANGE_DAYS[chart_range]), anchor


def _comparison_out(
    engine: Engine,
    isin: str,
    benchmark_key: str,
    intervals: Sequence[HoldingInterval],
) -> ComparisonOut:
    result = compute_comparison(
        engine, isin, benchmark_key=benchmark_key, intervals=intervals
    )
    return ComparisonOut(
        basis=result.basis,
        benchmark_key=result.benchmark_key,
        instrument_index=[
            IndexPointOut(date=p.on, index=p.index) for p in result.instrument_index
        ],
        benchmark_index=[
            IndexPointOut(date=p.on, index=p.index) for p in result.benchmark_index
        ],
        intervals=[
            IntervalExcessOut(
                start=row.start,
                end=row.end,
                instrument_return=row.instrument_return,
                benchmark_return=row.benchmark_return,
                excess=row.excess,
                reason=row.reason,
            )
            for row in result.intervals
        ],
        linked_instrument_return=result.linked_instrument_return,
        linked_benchmark_return=result.linked_benchmark_return,
        linked_excess=result.linked_excess,
        span=result.span,
    )


@router.get("/instruments", response_model=InstrumentListOut)
def list_instruments(engine: Engine = Depends(get_engine)) -> InstrumentListOut:
    return InstrumentListOut(
        items=_traded_instruments(engine),
        method=None,
        coverage="full",
    )


@router.get("/instruments/{isin}/chart", response_model=InstrumentChartOut)
def get_instrument_chart(
    isin: str,
    engine: Engine = Depends(get_engine),
    benchmarks: tuple[Benchmark, ...] = Depends(get_benchmarks),
    range: ChartRange = Query(default="1Y"),
    benchmark: str | None = Query(default=None),
) -> InstrumentChartOut:
    earliest = _earliest_trade_date(engine, isin)
    if earliest is None:
        raise HTTPException(status_code=404, detail=f"no instrument traded with ISIN {isin!r}")

    configured = {b.key: b for b in benchmarks}
    if benchmark is not None and benchmark not in configured:
        raise HTTPException(
            status_code=422,
            detail=(
                f"unknown benchmark {benchmark!r}; configured benchmarks are "
                f"{sorted(configured)}"
            ),
        )

    anchor = _latest_priced_day(engine, isin) or earliest
    requested_from, end = _window(range, earliest, anchor)
    # Clamped to inception, exactly as `analytics/valuation.py` clamps its own
    # `start` to the ledger's first day: a price line drawn back before the
    # instrument was ever traded reports an out-of-market stretch, with a
    # price return attached, for a period the owner had never heard of it --
    # rendered identically to a genuine sell-then-rebuy gap. A value for a day
    # the holding did not exist is a false claim, not a missing one.
    #
    # `max` rather than a bare assignment: anchoring the view on `earliest`
    # below already guarantees no earlier point exists, so this states the
    # invariant that clipping never reaches before inception rather than
    # relying on the anchor to keep holding it.
    start = max(requested_from, earliest)

    # The FULL history, never the requested window -- see this module's
    # docstring. Only `points` and `markers` below are clipped.
    view = instrument_price_view(engine, isin, start=earliest, end=end)

    comparison_out = (
        _comparison_out(engine, isin, benchmark, view.intervals)
        if benchmark is not None
        else None
    )

    points = [p for p in view.points if start <= p.on <= end]
    # Markers too: the chart's x-axis carries only the drawn dates, so a
    # marker outside them would vanish silently rather than draw off-screen.
    markers = [m for m in view.markers if start <= m.on <= end]

    return InstrumentChartOut(
        isin=view.isin,
        points=[
            PricePointOut(
                date=p.on, close_base=p.close_base, coverage=p.coverage, held=p.held
            )
            for p in points
        ],
        intervals=[
            IntervalOut(
                start=i.start, end=i.end, in_market=i.in_market, price_return=i.price_return
            )
            for i in view.intervals
        ],
        markers=[
            MarkerOut(
                date=m.on, side=m.side, quantity=m.quantity, price=m.price,
                fees=m.fees, position_after=m.position_after,
            )
            for m in markers
        ],
        comparison=comparison_out,
        requested_from=requested_from,
        clamped=requested_from < earliest,
        method=None,
        # The badge describes what was DRAWN, so it is rolled up over the
        # clipped points -- `view.coverage` covers days this response does not
        # carry. `worst_coverage` is `instrument_price_view`'s own helper, not
        # a second implementation of the same judgement.
        coverage=worst_coverage([p.coverage for p in points]) if points else MISSING,
    )


@router.get("/benchmarks", response_model=BenchmarkListOut)
def list_benchmarks(
    benchmarks: tuple[Benchmark, ...] = Depends(get_benchmarks),
) -> BenchmarkListOut:
    return BenchmarkListOut(
        items=[BenchmarkOut(key=b.key, name=b.name, ter=b.ter) for b in benchmarks],
        method=None,
        coverage="full",
    )

"""The portfolio's time-weighted return: differenced, chain-linked, and honest
about where it cannot be.

M6a sections 3 and 4. For each valuation day `d` with a predecessor:

             V(d) - V(d-1) - F(d)
    r(d)  =  --------------------          R  =  prod(1 + r(d)) - 1
                   V(d-1)

`V` is `ValuationPoint.value_base` -- holdings at the UNADJUSTED close plus cash
(M6a-2, M6a-3). This module names no close column; it inherits the lane from
`valuation.py`, and it must never import `total_return.py`: a cash balance
already holds every dividend, and an adjusted close holds them again, which is
parent doc Sec 7.5's double count.

`F` is the external flow (`flows.py`). Nothing else enters. Dividends and fees
are inside `V` through cash, and a trade moves value between cash and holdings
without changing `V`, so it needs no timing rule (M6a section 3.3). What survives
a trade is its fee and the gap between its price and the close -- exactly what
the owner gained or lost that day.

**Three rules the design states, and two it leaves to this file.**

1. Flows are effective at the close (M6a-5): a deposit on `d` is in `V(d)` and
   subtracted from the numerator, so it earns nothing on the day it lands.
2. `r(d)` is `None` when either close has no valuation. A return is a
   difference, so one unusable day breaks TWO links (M6a section 4).
3. No single figure spans a gap (M6a-7). The series is reported as contiguous
   runs, each with its own linked return; the window figure exists only when
   every link in the window has a return.
4. **A flow belongs to the link whose close it falls before.** The grid is
   weekdays and `cash_daily` applies every row dated on or before a day, so a
   Saturday deposit is in Monday's `V`. `F(d)` is every flow dated after the
   previous close and on or before `d`. By exact date, the deposit would be
   missed and reported as Monday's gain.
5. **A link needs capital.** Before the first close with `V > 0` there is nothing
   to measure (M6a section 3.4), so leading closes at or below zero are trimmed.
   The same fact inside the window -- an account emptied and refilled -- leaves
   `r(d)` no denominator. That link is `None` with its own reason and breaks the
   run exactly as a gap does: a figure chained across a stretch with no capital
   in it would be as much an invention as one chained across missing prices.

M6b consumes both outputs: the flows for MWR/XIRR, and the linked aggregate as
the figure contribution to return must sum to (M6a section 9).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import pairwise

from app.analytics.indexing import HUNDRED, ONE, IndexPoint
from app.analytics.quotes import MISSING, worst_coverage
from app.analytics.valuation import ValuationPoint
from app.models.types import Coverage

#: Every figure on a `PortfolioReturn` is time-weighted. Stated on the object
#: rather than assumed by the reader -- parent doc Sec 7.4: no endpoint returns
#: an unlabelled return, and TWR is the only figure compared against a benchmark.
BASIS = "time_weighted"
#: Which closes value the holdings, and that cash is in the figure.
LANE = "unadjusted_close_plus_cash"
#: How dividends reach this figure: as cash on the pay date, after withholding,
#: idle until the owner does something with them (M6a section 7).
DIVIDENDS = "held_as_cash_net_of_withholding"

_ZERO = Decimal("0.00")
_NOTHING_TO_MEASURE = (
    "fewer than two valuation days with capital in them; there is no return to measure"
)


@dataclass(frozen=True, slots=True)
class ReturnLink:
    """One day's return, measured from the previous valuation day's close."""

    on: date
    #: The close this link is measured FROM.
    since: date
    #: Net external flow dated in `(since, on]`. See rule 4.
    flow_base: Decimal
    #: `None`, never 0, when either close has no valuation or `since` had no capital.
    daily_return: Decimal | None
    #: The worse of the two closes' coverage.
    coverage: Coverage
    #: Why `daily_return` is `None`. Set exactly when it is.
    reason: str | None


@dataclass(frozen=True, slots=True)
class ReturnRun:
    """A contiguous stretch of measurable links, and the one figure it earns."""

    #: The close the run is measured from -- the day BEFORE its first link.
    start: date
    end: date
    links: tuple[ReturnLink, ...]
    #: `prod(1 + r) - 1` over `links`. Never `None`: a run has no gap by construction.
    linked_return: Decimal
    #: The worst coverage over every close in the run, `start` included. A figure
    #: built from stale or manual prices says so (M6a section 4).
    coverage: Coverage
    #: 100 at `start`, then one point per link.
    index: tuple[IndexPoint, ...]


@dataclass(frozen=True, slots=True)
class PortfolioReturn:
    basis: str
    lane: str
    dividends: str
    #: The first and last close measured, after leading closes with no capital
    #: are trimmed. `None` when there is nothing to measure.
    start: date | None
    end: date | None
    #: Every link, broken ones included.
    links: tuple[ReturnLink, ...]
    runs: tuple[ReturnRun, ...]
    #: `None` unless every link in the window has a return (rule 3).
    linked_return: Decimal | None
    #: How many separate stretches of broken links the window holds.
    gaps: int
    #: The worst close in the window; `missing` when there is nothing to measure.
    coverage: Coverage
    #: Why `linked_return` is `None`. Set exactly when it is.
    reason: str | None


def portfolio_returns(
    points: Sequence[ValuationPoint], flows: Mapping[date, Decimal]
) -> PortfolioReturn:
    """Link every close in `points` and report the runs, the gaps and the window.

    `points` is a valuation window in date order, as `value_series` returns it.
    `flows` is `flows.external_flows`'s per-calendar-day mapping and may hold
    days outside the window; only flows between two closes are used.
    """
    measured = _from_first_capital(points)
    if len(measured) < 2:
        return _nothing_to_measure(measured)

    links = tuple(
        _link(before, after, _flow_between(flows, before.on, after.on))
        for before, after in pairwise(measured)
    )
    runs = _runs(measured, links)
    gaps = _gap_count(links)
    return PortfolioReturn(
        basis=BASIS,
        lane=LANE,
        dividends=DIVIDENDS,
        start=measured[0].on,
        end=measured[-1].on,
        links=links,
        runs=runs,
        # No gap means every link has a return, which means exactly one run.
        linked_return=runs[0].linked_return if gaps == 0 else None,
        gaps=gaps,
        coverage=worst_coverage([point.coverage for point in measured]),
        reason=None if gaps == 0 else _window_reason(gaps, len(runs)),
    )


def _from_first_capital(points: Sequence[ValuationPoint]) -> tuple[ValuationPoint, ...]:
    """Drop leading closes KNOWN to hold no capital (rule 5).

    A leading close with no valuation at all is a coverage gap, not an empty
    account, and trimming it would silently shorten the window the reader asked
    for -- so trimming stops there.
    """
    for position, point in enumerate(points):
        if point.value_base is None or point.value_base > 0:
            return tuple(points[position:])
    return ()


def _flow_between(flows: Mapping[date, Decimal], since: date, through: date) -> Decimal:
    """Every flow dated after one close and on or before the next (rule 4)."""
    return sum((amount for on, amount in flows.items() if since < on <= through), _ZERO)


def _link(before: ValuationPoint, after: ValuationPoint, flow: Decimal) -> ReturnLink:
    """One day's return, or the reason there is none."""
    coverage = worst_coverage([before.coverage, after.coverage])
    value_before, value_after = before.value_base, after.value_base
    if value_before is None or value_after is None:
        unvalued = " or ".join(
            point.on.isoformat() for point in (before, after) if point.value_base is None
        )
        return _broken(before, after, flow, coverage, f"no valuation on {unvalued}")
    if value_before <= 0:
        return _broken(
            before, after, flow, coverage, f"no capital at the close of {before.on}"
        )
    return ReturnLink(
        on=after.on,
        since=before.on,
        flow_base=flow,
        daily_return=(value_after - value_before - flow) / value_before,
        coverage=coverage,
        reason=None,
    )


def _broken(
    before: ValuationPoint,
    after: ValuationPoint,
    flow: Decimal,
    coverage: Coverage,
    reason: str,
) -> ReturnLink:
    return ReturnLink(
        on=after.on, since=before.on, flow_base=flow, daily_return=None,
        coverage=coverage, reason=reason,
    )


def _runs(
    points: Sequence[ValuationPoint], links: Sequence[ReturnLink]
) -> tuple[ReturnRun, ...]:
    """Split `links` into maximal stretches with no broken link in them (rule 3)."""
    coverage_on = {point.on: point.coverage for point in points}
    runs: list[ReturnRun] = []
    stretch: list[tuple[ReturnLink, Decimal]] = []
    for item in links:
        if item.daily_return is not None:
            stretch.append((item, item.daily_return))
            continue
        if stretch:
            runs.append(_run(stretch, coverage_on))
        stretch = []
    if stretch:
        runs.append(_run(stretch, coverage_on))
    return tuple(runs)


def _run(
    stretch: Sequence[tuple[ReturnLink, Decimal]], coverage_on: Mapping[date, Coverage]
) -> ReturnRun:
    """Chain one stretch, indexed at 100 on the close it is measured from.

    The growth factor is carried by hand rather than through `indexing.link`
    because the index needs every intermediate level, not only the last one.
    """
    start = stretch[0][0].since
    growth = ONE
    index = [IndexPoint(on=start, index=HUNDRED)]
    for item, daily in stretch:
        growth *= ONE + daily
        index.append(IndexPoint(on=item.on, index=HUNDRED * growth))
    closes = [start, *(item.on for item, _ in stretch)]
    return ReturnRun(
        start=start,
        end=stretch[-1][0].on,
        links=tuple(item for item, _ in stretch),
        linked_return=growth - ONE,
        coverage=worst_coverage([coverage_on[on] for on in closes]),
        index=tuple(index),
    )


def _gap_count(links: Sequence[ReturnLink]) -> int:
    """Separate stretches of broken links. One unpriceable day breaks two
    adjacent links and is one gap, not two."""
    gaps = 0
    previous_broken = False
    for item in links:
        broken = item.daily_return is None
        if broken and not previous_broken:
            gaps += 1
        previous_broken = broken
    return gaps


def _window_reason(gaps: int, runs: int) -> str:
    if runs == 0:
        return "no link in this window could be measured"
    stretches = "stretch" if gaps == 1 else "stretches"
    run_word = "run" if runs == 1 else "runs"
    return (
        f"{gaps} unmeasurable {stretches} split this window into {runs} {run_word}; "
        "no single figure spans a gap"
    )


def _nothing_to_measure(measured: Sequence[ValuationPoint]) -> PortfolioReturn:
    """Fewer than two closes with capital: no link, so no return. `missing`
    rather than `full` -- no observations is not a well-covered set of them."""
    return PortfolioReturn(
        basis=BASIS,
        lane=LANE,
        dividends=DIVIDENDS,
        start=measured[0].on if measured else None,
        end=measured[-1].on if measured else None,
        links=(),
        runs=(),
        linked_return=None,
        gaps=0,
        coverage=MISSING,
        reason=_NOTHING_TO_MEASURE,
    )

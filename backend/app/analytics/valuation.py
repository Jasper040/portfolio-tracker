"""Value the portfolio by joining the ledger's half to the network's. No table.

M2 spec section 4. `position_daily` and `cash_daily` are derived and
deterministic; `price_daily` and `fx_daily` are fetched and dated. Valuation is
where the two meet, and it meets them at READ time.

There is deliberately no `valuation_daily`. A table would have two independent
triggers -- a ledger change and a price refresh -- either of which would silently
invalidate it, and there is no way to look at a stored valuation and tell whether
it is still true. The cost is a join per read rather than a lookup, which at this
portfolio's size is tens of thousands of rows in SQLite. M2 has no scale problem
to solve and should not pre-solve one.

Four rules decide what a day is worth, and each is a claim rather than a
convenience:

**Carry-forward is not separate machinery.** "The latest price dated on or before
this day" already carries a Friday close into Monday. The gap between that
price's date and the day being valued IS the staleness M2-3 requires recorded --
so nothing infers a price, it only says how old the one it used is. A price dated
AFTER the day is never used: that is not carry-forward, it is hindsight, and it
would make yesterday's chart move every time prices were fetched.

**Coverage is weighted by value, not by instrument count.** One large holding
going dark matters more than three small ones, and a count would say the
opposite.

**A manual component's staleness is not measured.** `config/manual_prices.csv` is
sparse by design -- an operator types a price when they have one -- so measuring
its age would pin every manual day at `partial` and `manual` would never be
reached at all, which parent doc Sec 8.1 forbids by requiring all four values to
mean something distinct. `partial` stays reachable because it is a statement
about PROVIDER staleness, and a stale provider price is a different problem from
a hand-maintained one.

**A day with an unpriceable holding has no value.** Not EUR 0, not the sum of
what happens to be priced. A total that quietly drops a position looks exactly
like a total that includes it, and `covered_pct` is `null` there too: there is no
denominator to take a fraction of.

Split in M3: the FX/coverage machinery moved to `quotes.py`, the unadjusted-close
readers to `prices.py`, and `current_positions` to `positions_snapshot.py`. This
file keeps only the daily series -- see those modules for why the cut falls
where it does.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import Engine, func
from sqlalchemy import select as sa_select
from sqlmodel import Session, select

from app.analytics.prices import PricePoint, price_history, priced
from app.analytics.quotes import (
    FULL,
    MISSING,
    PARTIAL,
    RatePoint,
    base_currency,
    rate_history,
    worst_coverage,
)
from app.models.ledger import CashDaily, PositionDaily
from app.models.types import Coverage

_ZERO = Decimal("0.00")
_ONE = Decimal("1")


@dataclass(frozen=True, slots=True)
class ValuationPoint:
    on: date
    #: `None` when any held instrument could not be priced. See the docstring.
    holdings_base: Decimal | None
    cash_base: Decimal
    value_base: Decimal | None
    coverage: Coverage
    #: The share of the day's HOLDINGS value that is fresh from a provider or
    #: hand-supplied. `None` exactly when `coverage` is `missing`.
    covered_pct: Decimal | None


@dataclass(frozen=True, slots=True)
class ValuationSeries:
    points: tuple[ValuationPoint, ...]
    start: date | None
    end: date | None
    #: What the caller asked for, kept so the UI can say the window was clamped.
    requested_from: date | None
    clamped: bool
    #: The worst coverage in the series. An envelope claiming `full` over a
    #: series with a missing day would be Sec 8.1's omission one level up.
    coverage: Coverage
    base_currency: str


# ---------------------------------------------------------------------------
# The series
# ---------------------------------------------------------------------------
def value_series(
    engine: Engine, *, start: date | None = None, end: date | None = None
) -> ValuationSeries:
    """The daily net portfolio value: holdings at market plus cash.

    Without `start`, the series begins at the first day a position existed
    (M2-7). With one, it begins there -- clamped to the ledger's own first day,
    never padded before it, because a zero portfolio value on a day the account
    did not exist is a false claim rather than a missing one. That clamp is what
    lets a reader ask for five years and get an honest answer whatever the
    ledger's depth.
    """
    with Session(engine) as session:
        base = base_currency(session)

        # The window is decided BEFORE anything is loaded, from three scalars,
        # because every load below depends on it (PT-49). Previously the whole
        # ledger was read and then filtered day by day, so a one-month request
        # cost exactly what a full-ledger one did.
        # One row, two bounds. Narrowing them together is also what tells the
        # type checker the ledger is non-empty: an empty `cash_daily` gives
        # `None` for both, and that is the only way either can be `None`.
        floor, last_cash = session.execute(
            sa_select(func.min(CashDaily.cash_date), func.max(CashDaily.cash_date))
        ).one()
        if floor is None or last_cash is None:
            return ValuationSeries(
                points=(), start=None, end=None, requested_from=start,
                clamped=False, coverage=FULL, base_currency=base,
            )
        first_position = session.execute(
            sa_select(func.min(PositionDaily.position_date))
        ).scalar()

        # `default_start` is the first day a POSITION existed and `floor` the
        # first day a CASH ROW did. They are different days and the difference
        # is deliberate: asked for everything, the series answers "my
        # portfolio"; asked for a window, it answers as far back as the ledger
        # goes. `frontend/src/lib/valuation-window.ts` refuses to narrow across
        # that gap for exactly this reason.
        default_start = first_position if first_position is not None else floor
        window_start = max(start, floor) if start is not None else default_start
        window_end = end or last_cash

        cash_rows = session.execute(
            sa_select(CashDaily.cash_date, CashDaily.balance_base)  # type: ignore[call-overload]
            .where(CashDaily.cash_date >= window_start, CashDaily.cash_date <= window_end)
            .order_by(CashDaily.cash_date)
        ).all()
        position_rows = session.exec(
            select(PositionDaily).where(
                PositionDaily.position_date >= window_start,
                PositionDaily.position_date <= window_end,
            )
        ).all()
        # Both carry in the last row BEFORE the window as well; see
        # `prices.price_history` for why dropping it would change a day's
        # coverage verdict rather than just its timing.
        prices = price_history(session, start=window_start, end=window_end)
        rates = rate_history(session, start=window_start, end=window_end)

    held: dict[date, list[PositionDaily]] = {}
    for row in position_rows:
        held.setdefault(row.position_date, []).append(row)

    # No date test in the loop: the query returned the window and nothing else.
    points: list[ValuationPoint] = [
        _value_day(day, held.get(day, []), balance, base, prices, rates)
        for day, balance in cash_rows
    ]

    return ValuationSeries(
        points=tuple(points),
        start=points[0].on if points else None,
        end=points[-1].on if points else None,
        requested_from=start,
        clamped=start is not None and start < floor,
        coverage=worst_coverage([point.coverage for point in points]),
        base_currency=base,
    )


def _value_day(
    day: date,
    holdings: Sequence[PositionDaily],
    cash: Decimal,
    base: str,
    prices: Mapping[str, list[PricePoint]],
    rates: Mapping[tuple[str, str], list[RatePoint]],
) -> ValuationPoint:
    total = _ZERO
    covered = _ZERO
    verdicts: list[Coverage] = []
    missing = False

    for holding in sorted(holdings, key=lambda row: row.isin):
        result = priced(
            holding.isin, day, holding.quantity, base=base, history=prices, rates=rates
        )
        verdicts.append(result.coverage)
        if result.value is None:
            missing = True
            continue

        total += result.value
        if result.coverage != PARTIAL:
            covered += result.value

    coverage = worst_coverage(verdicts)
    if missing:
        # No total, so no fraction of one either.
        return ValuationPoint(
            on=day, holdings_base=None, cash_base=cash, value_base=None,
            coverage=MISSING, covered_pct=None,
        )

    return ValuationPoint(
        on=day,
        holdings_base=total,
        cash_base=cash,
        value_base=total + cash,
        coverage=coverage,
        covered_pct=_ONE if total == 0 else covered / total,
    )

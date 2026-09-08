"""One instrument's price line, the days it was held, and what was traded.

M3 section 5.2. Reads `close_unadjusted` through `analytics/prices.py` and
NEVER the adjusted close: the markers on this line are executed prices, and a
dividend-adjusted series restates every historical close downward, so a marker
would float above the line by a margin that grows the further back you look --
largest exactly where the reader is least able to check it.

The comparison against a benchmark is the opposite case and lives in
`instrument_return.py`. The two modules do not import each other and
`test_no_double_count.py` asserts it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import Engine
from sqlmodel import Session, select

from app.analytics.prices import price_history, priced
from app.analytics.quotes import (
    MISSING,
    base_currency,
    rate_history,
    worst_coverage,
)
from app.domain.positions import weekdays
from app.models.ledger import PositionDaily, Transaction
from app.models.types import Coverage


@dataclass(frozen=True, slots=True)
class InstrumentPricePoint:
    on: date
    #: `None` when the day cannot be priced. Never 0 -- Sec 8.1.
    close_base: Decimal | None
    coverage: Coverage
    #: Whether a position was open. The line is drawn in two registers off this.
    held: bool


@dataclass(frozen=True, slots=True)
class Interval:
    start: date
    end: date
    in_market: bool
    #: `None` when either end of the interval could not be priced.
    price_return: Decimal | None


@dataclass(frozen=True, slots=True)
class Marker:
    on: date
    side: str
    quantity: Decimal
    price: Decimal
    fees: Decimal
    position_after: Decimal


@dataclass(frozen=True, slots=True)
class InstrumentPriceView:
    isin: str
    points: tuple[InstrumentPricePoint, ...]
    intervals: tuple[Interval, ...]
    markers: tuple[Marker, ...]
    coverage: Coverage


def _intervals(points: tuple[InstrumentPricePoint, ...]) -> tuple[Interval, ...]:
    """Alternating held and flat runs, each with its own end-to-end return."""
    if not points:
        return ()

    out: list[Interval] = []
    run_start = 0
    for i in range(1, len(points) + 1):
        ended = i == len(points) or points[i].held != points[run_start].held
        if not ended:
            continue
        a, z = points[run_start], points[i - 1]
        ret: Decimal | None = None
        if a.close_base is not None and z.close_base is not None and a.close_base != 0:
            ret = z.close_base / a.close_base - 1
        out.append(
            Interval(start=a.on, end=z.on, in_market=a.held, price_return=ret)
        )
        run_start = i
    return tuple(out)


def _markers(session: Session, isin: str, start: date, end: date) -> tuple[Marker, ...]:
    """Every real trade, in base currency, with the position it left behind.

    `is_economic` is the load-bearing filter. M0 records a split as a pair of
    legs flagged `is_economic=False` -- `domain/splits.py` reads exactly those
    to derive the ratio -- so without this clause a 10-for-1 split draws as a
    phantom sell of the old shares and a phantom buy of the new ones, on the
    one day the reader is most likely to be checking why the line moved.
    """
    rows = sorted(
        session.exec(
            select(Transaction).where(
                Transaction.isin == isin,
                Transaction.trade_date >= start,
                Transaction.trade_date <= end,
                Transaction.is_economic == True,  # noqa: E712 -- SQL, not Python
            )
        ).all(),
        key=lambda row: (row.trade_date, row.id),
    )
    out: list[Marker] = []
    running = Decimal("0")
    for row in rows:
        if row.txn_type not in ("BUY", "SELL"):
            continue
        if row.quantity is None or row.price_local is None:
            continue
        # The executed price in BASE currency, because the line it sits on is in
        # base. `fx_rate` is the broker's own rate for this trade -- units of the
        # local currency per 1 base, the same direction as domain.money.FxRate --
        # so divide. Using it rather than fx_daily is deliberate: this marker is
        # what the owner actually paid, not what the day's reference rate says
        # they would have.
        price_base = row.price_local
        if row.fx_rate is not None and row.fx_rate != 0:
            price_base = row.price_local / row.fx_rate
        running += row.quantity
        out.append(
            Marker(
                on=row.trade_date,
                side="BUY" if row.quantity > 0 else "SELL",
                quantity=abs(row.quantity),
                price=price_base,
                fees=row.fee_base,
                position_after=running,
            )
        )
    return tuple(out)


def instrument_price_view(
    engine: Engine, isin: str, *, start: date, end: date
) -> InstrumentPriceView:
    """One instrument's priced line over an inclusive window."""
    with Session(engine) as session:
        base = base_currency(session)
        prices = price_history(session)
        rates = rate_history(session)
        held_on = {
            row.position_date: row.quantity
            for row in session.exec(
                select(PositionDaily).where(PositionDaily.isin == isin)
            ).all()
        }
        markers = _markers(session, isin, start, end)

    points: list[InstrumentPricePoint] = []
    for day in weekdays(start, end):
        # Quantity 1: this is a price line, not a valuation. The arithmetic is
        # the same either way and the caller says which it meant.
        result = priced(
            isin, day, Decimal("1"), base=base, history=prices, rates=rates
        )
        held = held_on.get(day, Decimal("0")) > 0
        points.append(
            InstrumentPricePoint(
                on=day,
                close_base=result.value,
                coverage=result.coverage,
                held=held,
            )
        )

    frozen = tuple(points)
    return InstrumentPriceView(
        isin=isin,
        points=frozen,
        intervals=_intervals(frozen),
        markers=markers,
        coverage=worst_coverage([p.coverage for p in frozen]) if frozen else MISSING,
    )

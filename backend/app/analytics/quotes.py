"""Currency, staleness and coverage -- the machinery that is the same whichever
close you are reading.

This file names NEITHER close column, and that is the point rather than an
accident. `analytics/instrument_return.py` needs the FX conversion in here to
satisfy M3-7, and if the unadjusted-close readers lived here too it would
acquire a call path to the unadjusted close by importing FX -- quietly
defeating the guard in M3 section 3.2 through the back door. The readers live
next door in `prices.py` for exactly that reason. Do not merge these two files.

`Quote.close` is deliberately not named `close_unadjusted`: it holds whichever
close its caller read, which is what lets `in_base` serve both sides.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select as sa_select
from sqlmodel import Session, select

from app.models.ledger import Account
from app.models.market import FxDaily
from app.models.types import Coverage
from app.providers.base import MANUAL as MANUAL_SOURCE

#: How old a provider price may be before the day is `partial`. Four calendar
#: days absorbs a weekend plus one holiday (M2 spec section 7.2). A venue closing
#: for longer would show as `partial`, which is arguably correct and has not been
#: observed in the current data.
STALE_DAYS = 4

FULL: Coverage = "full"
PARTIAL: Coverage = "partial"
MANUAL: Coverage = "manual"
MISSING: Coverage = "missing"

#: Worst news first. `missing` outranks everything because it is the only value
#: that withholds a number. `partial` outranks `manual` because a stale provider
#: price is a fault; a hand-typed one is a choice.
#:
#: Annotated `dict[Coverage, int]` rather than left to inference. M3-5 made
#: `Coverage` a checked type precisely so a fifth value could not be introduced
#: without every consumer being told; this is the one table where an unchecked
#: dict would swallow that -- inference widens the key type to `str`, so a new
#: member would sail past mypy and `KeyError` at runtime, inside `max()`, in
#: whichever request happened to carry it.
_SEVERITY: dict[Coverage, int] = {FULL: 0, MANUAL: 1, PARTIAL: 2, MISSING: 3}


def worst_coverage(values: Sequence[Coverage]) -> Coverage:
    """The most severe coverage among `values`.

    **Empty is `full`, and that is a decision rather than a fallback.** This
    function reports the worst news among a set of observations, and an empty set
    carries no bad news: a day on which nothing was held is not a badly covered
    day. `valuation` depends on exactly that reading.

    A caller for whom emptiness is ITSELF the bad news must say so at the call
    site, because only the caller knows which kind of empty it has -- "no
    holdings to cover" and "no observations at all" are different facts that
    arrive here as the same empty list. `instrument_price` does say so, with an
    explicit `if frozen else MISSING`: a price line with no points has not
    covered anything. Both readings are correct; what would be wrong is leaving
    the choice to a default that cannot see which one it is answering.
    """
    return max(values, key=lambda value: _SEVERITY[value], default=FULL)


@dataclass(frozen=True, slots=True)
class Quote:
    close: Decimal
    currency: str
    on: date
    source: str


def base_currency(session: Session) -> str:
    account = session.exec(select(Account)).first()
    return account.base_currency if account is not None else "EUR"


@dataclass(frozen=True, slots=True)
class RatePoint:
    """One cached FX rate, as the two fields the lookups read. Not an `FxDaily`,
    for the reason `prices.PricePoint` gives."""

    rate_date: date
    rate: Decimal


def rate_history(session: Session) -> dict[tuple[str, str], list[RatePoint]]:
    """Every cached rate, grouped by currency pair and ascending by date.

    Ordered in SQL. `fx_daily` is UNIQUE on (from_ccy, to_ccy, rate_date), so
    the order is total -- see `prices.price_history`."""
    history: dict[tuple[str, str], list[RatePoint]] = {}
    # `sa_select` for the reason `prices.price_history` gives.
    # See `prices.price_history` for why this one call is ignored.
    rows = session.execute(
        sa_select(  # type: ignore[call-overload]
            FxDaily.from_ccy, FxDaily.to_ccy, FxDaily.rate_date, FxDaily.rate).order_by(
            FxDaily.from_ccy, FxDaily.to_ccy, FxDaily.rate_date
        )
    ).all()
    for from_ccy, to_ccy, rate_date, rate in rows:
        history.setdefault((from_ccy, to_ccy), []).append(RatePoint(rate_date, rate))
    return history


def _latest_rate_on_or_before(rows: Sequence[RatePoint], on: date) -> RatePoint | None:
    """The last rate dated on or before `on`. Binary search, for the reason
    `prices._latest_on_or_before` sets out -- same shape, same sorted input,
    same identical answer including on duplicate dates."""
    index = bisect_right(rows, on, key=lambda row: row.rate_date)
    return rows[index - 1] if index else None


def in_base(
    quote: Quote,
    quantity: Decimal,
    on: date,
    base: str,
    rates: Mapping[tuple[str, str], list[RatePoint]],
) -> tuple[Decimal, int] | None:
    """Value one holding in the base currency, plus the age of the older input.

    `None` when the currency cannot be converted: a foreign holding needs two
    facts, and having one of them is no answer at all.
    """
    gross = quantity * quote.close
    age = (on - quote.on).days
    if quote.currency == base:
        return gross, age

    rate_row = _latest_rate_on_or_before(rates.get((quote.currency, base), ()), on)
    if rate_row is None or rate_row.rate == 0:
        return None
    # `rate` is units of the quote currency per 1 unit of base -- the same
    # direction as `domain.money.FxRate`. Divide, never multiply.
    return gross / rate_row.rate, max(age, (on - rate_row.rate_date).days)


def classify(source: str, age: int) -> Coverage:
    if source == MANUAL_SOURCE:
        # Deliberately not aged. See the module docstring.
        return MANUAL
    return PARTIAL if age > STALE_DAYS else FULL

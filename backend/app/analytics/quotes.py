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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

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
_SEVERITY = {FULL: 0, MANUAL: 1, PARTIAL: 2, MISSING: 3}


def worst_coverage(values: Sequence[Coverage]) -> Coverage:
    """The most severe coverage among `values`. Empty means nothing to cover."""
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


def rate_history(session: Session) -> dict[tuple[str, str], list[FxDaily]]:
    history: dict[tuple[str, str], list[FxDaily]] = {}
    for row in session.exec(select(FxDaily)).all():
        history.setdefault((row.from_ccy, row.to_ccy), []).append(row)
    for rows in history.values():
        rows.sort(key=lambda row: row.rate_date)
    return history


def latest_rate_on_or_before(rows: Sequence[FxDaily], on: date) -> FxDaily | None:
    found: FxDaily | None = None
    for row in rows:
        if row.rate_date > on:
            break
        found = row
    return found


def in_base(
    quote: Quote,
    quantity: Decimal,
    on: date,
    base: str,
    rates: Mapping[tuple[str, str], list[FxDaily]],
) -> tuple[Decimal, int] | None:
    """Value one holding in the base currency, plus the age of the older input.

    `None` when the currency cannot be converted: a foreign holding needs two
    facts, and having one of them is no answer at all.
    """
    gross = quantity * quote.close
    age = (on - quote.on).days
    if quote.currency == base:
        return gross, age

    rate_row = latest_rate_on_or_before(rates.get((quote.currency, base), ()), on)
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

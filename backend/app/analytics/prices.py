"""Reading the unadjusted close out of the price cache, and pricing one holding.

The ONLY analytics module that names `close_unadjusted`, which is half of what
parent doc Sec 7.5 requires; `total_return.py` is the other half. Split out of
`valuation.py` in M3 so the instrument chart's price side and its return side
could each reach exactly one column -- see `quotes.py` for why the FX helpers
are not in here.

`priced()` is here rather than in `quotes.py`, and the difference matters. PT-33
proposed `quotes.priced(...)`, but that module names NEITHER close column on
purpose: it serves both lanes, and a `quotes.py` that imported this file would
hand the ADJUSTED lane a call path to the unadjusted close through the FX import
-- exactly the back door M3 split these two files to close, and exactly what
`quotes.py`'s own docstring says not to do. The shared helper therefore belongs
on the unadjusted side, which is this file, where it is already what everything
reaching it came for.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlmodel import Session, select

from app.analytics.quotes import MISSING, Quote, classify, in_base
from app.models.market import FxDaily, PriceDaily
from app.models.types import Coverage


def price_history(session: Session) -> dict[str, list[PriceDaily]]:
    history: dict[str, list[PriceDaily]] = {}
    for row in session.exec(select(PriceDaily)).all():
        history.setdefault(row.isin, []).append(row)
    for rows in history.values():
        rows.sort(key=lambda row: row.price_date)
    return history


def _latest_on_or_before(rows: Sequence[PriceDaily], on: date) -> PriceDaily | None:
    found: PriceDaily | None = None
    for row in rows:
        if row.price_date > on:
            break  # sorted, so nothing later can qualify
        found = row
    return found


def quote_for(
    isin: str, on: date, history: Mapping[str, list[PriceDaily]]
) -> Quote | None:
    row = _latest_on_or_before(history.get(isin, ()), on)
    if row is None:
        return None
    return Quote(
        close=row.close_unadjusted,
        currency=row.currency,
        on=row.price_date,
        source=row.source,
    )


@dataclass(frozen=True, slots=True)
class Priced:
    """What one holding was worth on one day, and how well that is known.

    Three states, and the callers need to tell them apart:

    * no quote at all -- `quote` is `None`;
    * a quote that could not be reached in the base currency, because the day
      had no rate -- `quote` is set and `value` is `None`;
    * priced -- both set.

    `value` is `None` exactly when `coverage` is `missing`. A holding that could
    not be priced has no value, not a value of zero: a total that quietly drops a
    position looks exactly like a total that includes it (Sec 8.1).
    """

    quote: Quote | None
    value: Decimal | None
    coverage: Coverage


def priced(
    isin: str,
    on: date,
    quantity: Decimal,
    *,
    base: str,
    history: Mapping[str, list[PriceDaily]],
    rates: Mapping[tuple[str, str], list[FxDaily]],
) -> Priced:
    """Price one holding on one day: quote, convert, and judge the staleness.

    Written three times before PT-33 -- in `valuation._value_day`, in
    `positions_snapshot`, and in `instrument_price` -- by three implementers who
    could not see each other's work. The steps are identical every time and the
    order is load-bearing: a `None` from either the quote or the conversion is
    `missing`, and only a value that survived both gets classified, because
    `classify` answers "how old is this" and there is nothing to age when there
    is no answer.

    `quantity` is 1 for a price line, which is not a valuation; the arithmetic is
    the same and the caller says which it meant.
    """
    quote = quote_for(isin, on, history)
    if quote is None:
        return Priced(quote=None, value=None, coverage=MISSING)

    converted = in_base(quote, quantity, on, base, rates)
    if converted is None:
        return Priced(quote=quote, value=None, coverage=MISSING)

    value, age = converted
    return Priced(quote=quote, value=value, coverage=classify(quote.source, age))

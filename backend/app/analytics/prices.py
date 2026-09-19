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

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select as sa_select
from sqlmodel import Session

from app.analytics.quotes import MISSING, Quote, RatePoint, classify, in_base
from app.models.market import PriceDaily
from app.models.types import Coverage


@dataclass(frozen=True, slots=True)
class PricePoint:
    """One cached close, as the four fields the lookups actually read.

    Not a `PriceDaily`. Hydrating the ORM model was the whole remaining cost of
    a valuation once the linear scans became binary searches: a full-ledger run
    built 52,214 SQLModel instances, and merely parsing the UUID primary keys --
    which nothing on this path ever looks at -- took longer than the SQL. A
    frozen slotted dataclass carries the same four values with none of that.
    """

    price_date: date
    close_unadjusted: Decimal
    currency: str
    source: str


def price_history(session: Session) -> dict[str, list[PricePoint]]:
    """Every cached close, grouped by ISIN and ascending by date.

    Ordered in SQL rather than in Python. `price_daily` carries a UNIQUE
    constraint on (isin, price_date), so the order is total and no tie-break is
    left to chance -- which is what makes it safe to drop the stable Python sort
    this replaced.
    """
    history: dict[str, list[PricePoint]] = {}
    # `sa_select` rather than SQLModel's: the latter is overloaded for entities
    # and a handful of columns, and a five-column projection matches none of
    # them. This is the only place in the app that projects columns instead of
    # loading a model, which is why it is also the only place that departs from
    # `session.exec`.
    # The ignore is an upstream typing gap, not a silenced error. SQLModel
    # annotates a model attribute as its plain Python type rather than as
    # `Mapped[...]`, so these arrive at `select` as `str`/`date`/`Decimal` --
    # and `date` and `Decimal` are not among the scalar types its overloads
    # accept. The query itself is ordinary SQLAlchemy Core and runs correctly;
    # only the signature cannot describe it.
    rows = session.execute(
        sa_select(  # type: ignore[call-overload]
            PriceDaily.isin,
            PriceDaily.price_date,
            PriceDaily.close_unadjusted,
            PriceDaily.currency,
            PriceDaily.source,
        ).order_by(PriceDaily.isin, PriceDaily.price_date)
    ).all()
    for isin, price_date, close, currency, source in rows:
        history.setdefault(isin, []).append(
            PricePoint(price_date, close, currency, source)
        )
    return history


def _latest_on_or_before(rows: Sequence[PricePoint], on: date) -> PricePoint | None:
    """The last row dated on or before `on`, or `None` if the cache starts later.

    Binary search, not a walk. `rows` is already sorted by `price_date`, and the
    walk this replaced was the single most expensive thing the valuation did: it
    ran once per holding per day, and every `row.price_date` in it was an
    INSTRUMENTED SQLAlchemy attribute read rather than a plain field access.
    Profiling a full-ledger valuation counted 8.4 million of them, 3.4 of the
    3.6 seconds it took.

    `bisect_right` touches about log2(n) rows instead of n -- eleven rather than
    two thousand -- and the answer is identical by construction: it returns the
    insertion point after every row whose key is <= `on`, so the row before it is
    the last one that qualifies. Duplicate dates resolve the same way too, to the
    last of them, because `bisect_right` goes past equal keys and the walk kept
    overwriting `found`.
    """
    index = bisect_right(rows, on, key=lambda row: row.price_date)
    return rows[index - 1] if index else None


def quote_for(
    isin: str, on: date, history: Mapping[str, list[PricePoint]]
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
    history: Mapping[str, list[PricePoint]],
    rates: Mapping[tuple[str, str], list[RatePoint]],
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

"""Reading the unadjusted close out of the price cache.

The ONLY analytics module that names `close_unadjusted`, which is half of what
parent doc Sec 7.5 requires; `total_return.py` is the other half. Split out of
`valuation.py` in M3 so the instrument chart's price side and its return side
could each reach exactly one column -- see `quotes.py` for why the FX helpers
are not in here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

from sqlmodel import Session, select

from app.analytics.quotes import Quote
from app.models.market import PriceDaily


def price_history(session: Session) -> dict[str, list[PriceDaily]]:
    history: dict[str, list[PriceDaily]] = {}
    for row in session.exec(select(PriceDaily)).all():
        history.setdefault(row.isin, []).append(row)
    for rows in history.values():
        rows.sort(key=lambda row: row.price_date)
    return history


def latest_on_or_before(rows: Sequence[PriceDaily], on: date) -> PriceDaily | None:
    found: PriceDaily | None = None
    for row in rows:
        if row.price_date > on:
            break  # sorted, so nothing later can qualify
        found = row
    return found


def quote_for(
    isin: str, on: date, history: Mapping[str, list[PriceDaily]]
) -> Quote | None:
    row = latest_on_or_before(history.get(isin, ()), on)
    if row is None:
        return None
    return Quote(
        close=row.close_unadjusted,
        currency=row.currency,
        on=row.price_date,
        source=row.source,
    )

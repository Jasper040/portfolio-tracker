"""The daily net portfolio value.

`from` and `to` are optional. Without them the series starts at the first day a
position existed (M2-7), which is the answer to "show me my portfolio". With
them it starts where the caller asked, clamped to the ledger's own first day --
so a reader who selects five years is answered with everything the ledger has
and told the window was shortened, rather than being handed five years of padded
zeros. A zero portfolio value on a day the account did not exist is a false
claim, not a missing one.

`method` is `None` in the envelope. See `ValuationSeriesOut`.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Engine

from app.analytics.valuation import value_series
from app.api.routes_transactions import get_engine
from app.api.schemas import ValuationPointOut, ValuationSeriesOut

router = APIRouter(prefix="/api", tags=["valuation"])

@router.get("/valuation", response_model=ValuationSeriesOut)
def get_valuation(
    engine: Engine = Depends(get_engine),
    # `from` is a Python keyword, so the parameter is aliased rather than
    # renamed: the query string is what the API's readers see.
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = Query(default=None),
) -> ValuationSeriesOut:
    if from_ is not None and to is not None and from_ > to:
        raise HTTPException(
            status_code=422, detail=f"from {from_} is after to {to}"
        )

    series = value_series(engine, start=from_, end=to)
    return ValuationSeriesOut(
        items=[
            ValuationPointOut(
                date=point.on,
                holdings_base=point.holdings_base,
                cash_base=point.cash_base,
                value_base=point.value_base,
                coverage=point.coverage,
                covered_pct=point.covered_pct,
            )
            for point in series.points
        ],
        start=series.start,
        end=series.end,
        requested_from=series.requested_from,
        clamped=series.clamped,
        base_currency=series.base_currency,
        method=None,
        coverage=series.coverage,
    )

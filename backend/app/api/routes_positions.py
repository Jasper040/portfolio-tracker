"""Open holdings with market value and unrealised P&L.

`method` is required, exactly as on `/api/lots`: the cost basis comes from `lot`,
and FIFO, LIFO and HIFO produce three different ones from identical rows. The
share COUNT does not depend on it -- `position_daily` has no method column
because M1 proved it cannot -- so a row here joins one method-dependent figure to
one method-independent one, and the envelope says which method it used.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import Engine

from app.analytics.valuation import current_positions
from app.api.routes_transactions import get_engine
from app.api.schemas import LotMethod, PositionOut, PositionsOut

router = APIRouter(prefix="/api", tags=["positions"])

@router.get("/positions", response_model=PositionsOut)
def list_positions(
    method: LotMethod, engine: Engine = Depends(get_engine)
) -> PositionsOut:
    snapshot = current_positions(engine, method)
    return PositionsOut(
        items=[
            PositionOut(
                isin=item.isin,
                product_name=item.product_name,
                currency=item.currency,
                quantity=item.quantity,
                cost_basis=item.cost_basis,
                charges_base=item.charges_base,
                price=item.price,
                price_date=item.price_date,
                source=item.source,
                market_value_base=item.market_value_base,
                gross_unrealised_base=item.gross_unrealised_base,
                unrealised_base=item.unrealised_base,
                unrealised_pct=item.unrealised_pct,
                coverage=item.coverage,
            )
            for item in snapshot.items
        ],
        as_of=snapshot.as_of,
        total_cost_basis=snapshot.total_cost_basis,
        total_market_value_base=snapshot.total_market_value_base,
        total_unrealised_base=snapshot.total_unrealised_base,
        base_currency=snapshot.base_currency,
        method=method,
        coverage=snapshot.coverage,
    )

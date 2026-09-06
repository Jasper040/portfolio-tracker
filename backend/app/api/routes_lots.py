"""Lot and closure endpoints.

`method` is a required query parameter, not a defaulted one. FIFO, LIFO and HIFO
produce three different realised figures from identical rows (design doc Sec 7.1),
so answering without being asked which one would put a number under a label that
did not earn it -- the failure Sec 9.2 makes structurally impossible in the
response envelope.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Engine, func
from sqlmodel import Session, select

from app.api.routes_transactions import get_engine
from app.api.schemas import ClosureOut, ClosurePage, LotMethod, LotOut, LotPage
from app.models.ledger import Lot, LotClosure

router = APIRouter(prefix="/api", tags=["lots"])


@router.get("/lots", response_model=LotPage)
def list_lots(
    method: LotMethod,
    engine: Engine = Depends(get_engine),
    isin: str | None = None,
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
) -> LotPage:
    with Session(engine) as session:
        count_stmt = select(func.count()).select_from(Lot).where(Lot.method == method)
        page_stmt = (
            select(Lot)
            .where(Lot.method == method)
            .order_by(Lot.isin, Lot.opened_on, Lot.source_ref)  # type: ignore[arg-type]
        )
        if isin:
            count_stmt = count_stmt.where(Lot.isin == isin)
            page_stmt = page_stmt.where(Lot.isin == isin)

        total = session.exec(count_stmt).one()
        rows = session.exec(page_stmt.limit(limit).offset(offset)).all()

    return LotPage(
        items=[LotOut.from_model(row) for row in rows],
        total=total,
        method=method,
        # Every lot came from the ledger and nothing here depends on an external
        # series that could be missing. Market value, which does, is M2.
        coverage="full",
    )


@router.get("/closures", response_model=ClosurePage)
def list_closures(
    method: LotMethod,
    engine: Engine = Depends(get_engine),
    isin: str | None = None,
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
) -> ClosurePage:
    with Session(engine) as session:
        count_stmt = (
            select(func.count()).select_from(LotClosure).where(LotClosure.method == method)
        )
        page_stmt = (
            select(LotClosure)
            .where(LotClosure.method == method)
            .order_by(
                LotClosure.closed_on.desc(),  # type: ignore[attr-defined]
                LotClosure.id,  # type: ignore[arg-type]
            )
        )
        if isin:
            count_stmt = count_stmt.where(LotClosure.isin == isin)
            page_stmt = page_stmt.where(LotClosure.isin == isin)

        total = session.exec(count_stmt).one()
        rows = session.exec(page_stmt.limit(limit).offset(offset)).all()

    return ClosurePage(
        items=[ClosureOut.from_model(row) for row in rows],
        total=total,
        method=method,
        coverage="full",
    )

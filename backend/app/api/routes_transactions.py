from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import Engine, func
from sqlmodel import Session, select

from app.api.schemas import TransactionOut, TransactionPage
from app.models.ledger import Transaction

router = APIRouter(prefix="/api", tags=["transactions"])


def get_engine(request: Request) -> Engine:
    """The engine is wired onto app.state at construction time.

    Deliberately not `dependency_overrides`: that is FastAPI's test-seam and using
    it for production wiring leaves no seam left for tests to use.
    """
    engine: Engine = request.app.state.engine
    return engine


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/transactions", response_model=TransactionPage)
def list_transactions(
    engine: Engine = Depends(get_engine),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    isin: str | None = None,
) -> TransactionPage:
    with Session(engine) as session:
        count_stmt = select(func.count()).select_from(Transaction)
        # Ordering must be a TOTAL order or offset/limit can skip or repeat a row at a
        # page boundary. trade_date is not unique across rows, and source_ref is only
        # unique per source -- the DB constraint is UNIQUE(source, source_ref), and a
        # second source (SnapTrade) is a planned milestone. The primary key settles it.
        page_stmt = select(Transaction).order_by(
            Transaction.trade_date.desc(),  # type: ignore[attr-defined]
            Transaction.source_ref,
            Transaction.id,  # type: ignore[arg-type]
        )
        if isin:
            count_stmt = count_stmt.where(Transaction.isin == isin)
            page_stmt = page_stmt.where(Transaction.isin == isin)

        total = session.exec(count_stmt).one()
        rows = session.exec(page_stmt.limit(limit).offset(offset)).all()

    return TransactionPage(
        items=[TransactionOut.from_model(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )

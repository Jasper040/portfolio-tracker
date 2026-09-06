"""Recompute lots and closures from the ledger.

Design doc Sec 11.2 #5. `rebuild()` is the claim that every derived number in this
app can be recomputed from the ledger alone -- which is what makes a parser fix
propagate to realised P&L with no manual step, and what makes a disagreement
between two figures resolvable rather than a matter of opinion.

The claim is only worth something if it is checked, so this module does two things
beyond the arithmetic. It DELETES the derived rows for the method it is rebuilding
before writing new ones, so a rebuild is a replacement rather than an accumulation.
And it asserts `Sigma attributed charges == Sigma ledger charges` (Sec 11.2 #4)
before committing -- in production code, not only in tests, because the failure it
guards against is a cent lost in apportionment, which no reader would ever spot.

All arithmetic lives in `domain/`. This module reads rows, calls three pure passes
and writes the answers.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import Engine
from sqlmodel import Session, select

from app.domain.lots import LotMethod, match_lots
from app.domain.orders import charges_of, is_share_movement, to_lot_transactions
from app.domain.splits import apply_splits, derive_splits
from app.models.ledger import Lot, LotClosure, Transaction

_ZERO = Decimal("0.00")


class ChargeMismatch(RuntimeError):
    """Attributed charges did not equal the ledger's. Nothing was written."""


@dataclass(frozen=True, slots=True)
class RebuildResult:
    method: LotMethod
    lots: int
    closures: int
    charges_attributed: Decimal
    charges_in_ledger: Decimal


def _ledger_charges(rows: list[Transaction]) -> Decimal:
    """What the broker actually took, on the rows lot matching is allowed to see.

    Both halves are borrowed from `domain/orders.py` rather than restated here:
    `is_share_movement` decides which rows count, and `charges_of` turns a row's
    debits into money paid. That module owns the sign flip and states that it
    happens exactly once; a second copy of either here would be free to drift from
    the side of the equality that `to_lot_transactions` actually attributes, and
    the drift would surface as a `ChargeMismatch` naming apportionment -- the one
    place that would not actually be broken.
    """
    total = _ZERO
    for row in rows:
        if not is_share_movement(row):
            continue
        total += charges_of(row).total
    return total


def rebuild(engine: Engine, method: LotMethod) -> RebuildResult:
    """Recompute `lot` and `lot_closure` for one method, replacing what is there."""
    with Session(engine) as session:
        rows = list(session.exec(select(Transaction)).all())

    splits = derive_splits(rows)
    fills_by_isin = to_lot_transactions(rows)

    lots: list[Lot] = []
    closures: list[LotClosure] = []
    attributed = _ZERO

    for isin in sorted(fills_by_isin):
        adjusted = apply_splits(
            fills_by_isin[isin], [s for s in splits if s.isin == isin]
        )
        result = match_lots(adjusted, method)

        for open_lot in result.open_lots:
            attributed += open_lot.charges.total
            lots.append(
                Lot(
                    id=uuid4(),
                    method=method,
                    isin=isin,
                    source_ref=open_lot.id,
                    opened_on=open_lot.opened_on,
                    quantity=open_lot.quantity,
                    price=open_lot.price,
                    cost_basis=open_lot.cost_basis,
                    commission=open_lot.charges.commission,
                    autofx=open_lot.charges.autofx,
                    tax=open_lot.charges.tax,
                )
            )

        for closure in result.closures:
            attributed += closure.charges.total
            closures.append(
                LotClosure(
                    id=uuid4(),
                    method=method,
                    isin=isin,
                    lot_source_ref=closure.lot_id,
                    sale_source_ref=closure.sale_id,
                    opened_on=closure.opened_on,
                    closed_on=closure.closed_on,
                    quantity=closure.quantity,
                    open_price=closure.open_price,
                    close_price=closure.close_price,
                    gross_pnl=closure.gross_pnl,
                    commission=closure.charges.commission,
                    autofx=closure.charges.autofx,
                    tax=closure.charges.tax,
                    pnl=closure.pnl,
                    holding_days=closure.holding_days,
                    return_pct=closure.return_pct,
                    annualised_return=closure.annualised_return,
                )
            )

    in_ledger = _ledger_charges(rows)
    if attributed != in_ledger:
        raise ChargeMismatch(
            f"attributed {attributed} but the ledger holds {in_ledger}; "
            "nothing was written"
        )

    with Session(engine) as session:
        for stale_lot in session.exec(select(Lot).where(Lot.method == method)).all():
            session.delete(stale_lot)
        for stale in session.exec(
            select(LotClosure).where(LotClosure.method == method)
        ).all():
            session.delete(stale)
        session.flush()
        for lot in lots:
            session.add(lot)
        for closure_row in closures:
            session.add(closure_row)
        session.commit()

    return RebuildResult(
        method=method,
        lots=len(lots),
        closures=len(closures),
        charges_attributed=attributed,
        charges_in_ledger=in_ledger,
    )

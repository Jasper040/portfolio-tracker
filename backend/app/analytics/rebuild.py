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

M2 adds two more derived tables, `position_daily` and `cash_daily`, and
deliberately adds no third. There is no `valuation_daily`: valuation depends on a
fetched price, and a table that did would make this function's answer depend on
when it ran. Everything written here stays a pure function of the ledger, which
is the entire reason the price cache lives on the other side of the line and is
never touched from this module (M2 spec section 4).

`through` is why that still holds with a daily series in it. The ledger says when
you last traded, not when you last held, so the series needs an end date the
ledger cannot supply -- and taking it as a parameter, defaulting to the last
trade date, keeps `rebuild(engine, method)` a function of its inputs while
letting the CLI ask for "up to today".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import Engine
from sqlmodel import Session, select

from app.domain.lots import LotMethod, match_lots
from app.domain.orders import charges_of, is_share_movement, to_lot_transactions
from app.domain.positions import daily_series
from app.domain.splits import apply_splits, derive_splits
from app.models.ledger import CashDaily, Lot, LotClosure, PositionDaily, Transaction

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
    position_days: int
    cash_days: int


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


def rebuild(
    engine: Engine, method: LotMethod, *, through: date | None = None
) -> RebuildResult:
    """Recompute the derived tables for one method, replacing what is there.

    `through` is the last day of the daily series. `None` means the ledger's own
    last trade date, which keeps this deterministic; the CLI passes today's date
    so a position held since the last trade keeps appearing on the chart.
    """
    with Session(engine) as session:
        rows = list(session.exec(select(Transaction)).all())

    window_end = through or max((row.trade_date for row in rows), default=date.min)
    series = daily_series(rows, through=window_end)

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

        # A sale whose buys predate the export window matches no lot, so no closure
        # carries its charges. They still left the account, and both sides of
        # `Sum(attributed) == Sum(ledger)` have to see them or a single such sale
        # would refuse the rebuild for every instrument at once.
        attributed += result.unmatched_charges.total

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

        for stale_position in session.exec(select(PositionDaily)).all():
            session.delete(stale_position)
        for stale_cash in session.exec(select(CashDaily)).all():
            session.delete(stale_cash)
        session.flush()
        for point in series.positions:
            session.add(
                PositionDaily(
                    id=uuid4(),
                    position_date=point.on,
                    isin=point.isin,
                    quantity=point.quantity,
                )
            )
        for cash_point in series.cash:
            session.add(
                CashDaily(
                    id=uuid4(),
                    cash_date=cash_point.on,
                    balance_base=cash_point.balance_base,
                )
            )
        session.commit()

    return RebuildResult(
        method=method,
        lots=len(lots),
        closures=len(closures),
        charges_attributed=attributed,
        charges_in_ledger=in_ledger,
        position_days=len(series.positions),
        cash_days=len(series.cash),
    )

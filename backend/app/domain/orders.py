"""Ledger rows into economic orders, and orders into matchable fills.

Design doc Sec 6.4. DeGiro charges commission once per ORDER and books it on one
arbitrary fill row: the real export's CASTOR order is `+25` with a blank fee beside
`+75` charged EUR 2.00. Left where it landed, one fill carries a 2.67% cost and the
other none -- and a HIFO matcher choosing between them would be choosing on an
artefact of DeGiro's bookkeeping rather than on price.

So charges are pooled per order and split back across the fills pro rata by
quantity. `apportion_charges` makes the split exact, which is what lets the Sec 11.2
invariant hold as an equality rather than a tolerance.

Two conversions happen here and nowhere else:

* **Sign.** The ledger stores a charge as a debit (negative). The domain layer
  counts money paid (positive).
* **Currency.** `price_local` is USD on a US trade. Everything downstream is EUR, so
  the price handed to the matcher is `value_base / quantity` -- the euro amount the
  broker itself recorded, divided by the shares it bought. Deriving it from
  `price_local` and `fx_rate` instead would recompute a number the export already
  states, which Sec 5.4 forbids.

Pure: rows in, fills out. `LedgerRow` is a Protocol, so `domain/` imports no ORM.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Protocol

from app.domain.charges import Charges, apportion_charges
from app.domain.lots import LotTransaction

_ZERO = Decimal("0.00")


class LedgerRow(Protocol):
    """Exactly the fields this module reads off a `Transaction`.

    A Protocol rather than an import: `domain/` is pure by design (Sec 4.1), and a
    structural type keeps it that way while still type-checking against the ORM row
    the caller actually passes.
    """

    source_ref: str
    isin: str | None
    trade_date: date
    trade_time: str | None
    txn_type: str
    quantity: Decimal | None
    price_local: Decimal | None
    value_base: Decimal | None
    fee_base: Decimal
    autofx_fee_base: Decimal | None
    tax_base: Decimal
    order_ref: str | None
    is_economic: bool


def _order_key(row: LedgerRow) -> tuple[str, str, str]:
    """`(order_ref, trade_datetime, isin)` with a synthetic fallback (Sec 6.4).

    A blank order id falls back to the row's own date and time. Two blank-id rows at
    different times are two orders: pooling them would spread one trade's commission
    over another's fills.
    """
    isin = row.isin or ""
    stamp = f"{row.trade_date.isoformat()}T{row.trade_time or ''}"
    return (row.order_ref or f"synthetic:{stamp}:{isin}", stamp, isin)


def _charges_of(row: LedgerRow) -> Charges:
    """Ledger debits into money paid."""
    return Charges(
        commission=-row.fee_base,
        autofx=-(row.autofx_fee_base or _ZERO),
        tax=-row.tax_base,
    )


def is_share_movement(row: LedgerRow) -> bool:
    """The single definition of "a row lot matching is allowed to see".

    A row counts only if it is economic (M0's corporate-action quarantine has not
    flagged it), names an instrument, moved a non-zero quantity of it, and states
    the base-currency value the broker recorded for that movement. `analytics/rebuild.py`
    (Task 6) imports this same predicate to decide which rows count toward the
    standing invariant `Sum(attributed charges) == Sum(ledger charges)` -- a
    definition that drifted between the two call sites would let a row that counts
    toward one side of that equality but not the other abort every rebuild with a
    ChargeMismatch that names the wrong cause.
    """
    return (
        row.is_economic
        and row.isin is not None
        and row.quantity is not None
        and row.quantity != 0
        and row.value_base is not None
    )


def to_lot_transactions(rows: Sequence[LedgerRow]) -> dict[str, list[LotTransaction]]:
    """Group `rows` into orders, attribute charges, and return fills per ISIN.

    Each list is chronological, because `match_lots` raises on unordered input
    rather than silently producing a plausible wrong basis.
    """
    orders: dict[tuple[str, str, str], list[LedgerRow]] = defaultdict(list)
    for row in rows:
        if is_share_movement(row):
            orders[_order_key(row)].append(row)

    by_isin: dict[str, list[LotTransaction]] = defaultdict(list)
    for fills in orders.values():
        pooled = Charges.zero()
        for row in fills:
            pooled = pooled + _charges_of(row)

        # Quantity, not value: the two fills of one order are the same instrument at
        # nearly the same price, and quantity is the thing DeGiro's own per-order
        # commission is indifferent to.
        weights = [abs(row.quantity or _ZERO) for row in fills]
        for row, share in zip(fills, apportion_charges(pooled, weights), strict=True):
            quantity = row.quantity or _ZERO
            value = row.value_base or _ZERO
            by_isin[str(row.isin)].append(
                LotTransaction(
                    id=row.source_ref,
                    trade_date=row.trade_date,
                    side="BUY" if quantity > 0 else "SELL",
                    quantity=abs(quantity),
                    price=abs(value) / abs(quantity),
                    charges=share,
                )
            )

    return {
        isin: sorted(fills, key=lambda fill: (fill.trade_date, fill.id))
        for isin, fills in by_isin.items()
    }

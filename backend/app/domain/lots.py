"""Lot matching: FIFO, LIFO and HIFO.

A sale does not close "a position", it closes specific PURCHASE LOTS, and which
lots it picks decides the realised P&L. Selling 50 ORN bought at 8,90 and 12,40
realises a different number under FIFO than under HIFO from identical ledger rows.
That is why the method is a first-class input everywhere -- design doc Sec 7.1, and
Sec 9.2 for why every response carrying a realised figure must name it.

Pure by design (Sec 4.1): no ORM imports, no network, no `datetime.now()`. This is
what makes the hand-computed fixtures of Sec 11 possible and `rebuild()` provably
deterministic.

Everything is `Decimal`. Quantities subtract exactly, so a fully consumed lot lands
on exactly zero and the epsilon comparison a float implementation needs disappears.

Charges are attributed in a second pass rather than inline, because the invariant
`Σ attributed charges == Σ ledger charges` (Sec 11.2) cannot be met by dividing as
you go: a lot split three ways loses a cent to rounding on every closure. Matching
first, then apportioning each transaction's charges across everything it touched,
makes the sum exact by construction. Charges are never capitalised into the cost
basis (Sec 6.4): basis is quantity times price, full stop, and charges are reported
alongside it so what the stock did, what the broker took, and what is left all stay
visible as three separate numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from app.domain.charges import Charges, apportion_charges

LotMethod = Literal["FIFO", "LIFO", "HIFO"]

LOT_METHODS: tuple[LotMethod, ...] = ("FIFO", "LIFO", "HIFO")

_DAYS_PER_YEAR = Decimal("365")


@dataclass(frozen=True, slots=True)
class LotTransaction:
    """One economic buy or sell, with its order-level charges already attributed.

    Grouping fill rows into orders and splitting an order's charges across its fills
    happens upstream in `domain.orders`; by the time a transaction reaches the
    matcher its `charges` are its own.
    """

    id: str
    trade_date: date
    side: Literal["BUY", "SELL"]
    quantity: Decimal
    price: Decimal
    charges: Charges


@dataclass(frozen=True, slots=True)
class OpenLot:
    """Purchase quantity still held, with the charges belonging to that quantity."""

    id: str
    opened_on: date
    quantity: Decimal
    price: Decimal
    charges: Charges

    @property
    def cost_basis(self) -> Decimal:
        """What the shares cost. Charges are NOT capitalised (Sec 6.4).

        Named `cost_basis` rather than `cost` deliberately: the old name meant
        price-plus-fees, and a silent change of meaning behind an unchanged name is
        how a wrong number survives a refactor.
        """
        return self.quantity * self.price


@dataclass(frozen=True, slots=True)
class Closure:
    """One matched (buy lot, sale) pair.

    A sale spanning three lots produces three closures; a lot sold in three
    tranches also produces three.
    """

    lot_id: str
    #: The SALE's id. A closure is a pair, and carrying only the buy would leave
    #: half of it untraceable back to the ledger.
    sale_id: str
    opened_on: date
    open_price: Decimal
    closed_on: date
    close_price: Decimal
    quantity: Decimal
    #: Pro-rata charges from BOTH legs of the round trip.
    charges: Charges

    @property
    def gross_pnl(self) -> Decimal:
        """What the stock did, before the broker took anything."""
        return self.quantity * (self.close_price - self.open_price)

    @property
    def pnl(self) -> Decimal:
        """Realised P&L in base currency, net of both legs' charges."""
        return self.gross_pnl - self.charges.total

    @property
    def holding_days(self) -> int:
        return (self.closed_on - self.opened_on).days

    @property
    def return_pct(self) -> Decimal | None:
        """Return on the capital the lot tied up. `None` when that was zero."""
        basis = self.quantity * self.open_price
        if basis == 0:
            return None
        return self.pnl / basis

    @property
    def annualised_return(self) -> Decimal | None:
        """Holding-period return scaled to a year.

        `None` rather than a number in the two cases where one would be a lie: a
        same-day round trip, which has no period to annualise over, and a total
        loss, whose annualised equivalent is a root of a negative number.
        Presentation decides whether a short period is worth showing at all.
        """
        ret = self.return_pct
        if ret is None or self.holding_days <= 0:
            return None
        growth = Decimal(1) + ret
        if growth <= 0:
            return None
        try:
            return growth ** (_DAYS_PER_YEAR / Decimal(self.holding_days)) - Decimal(1)
        except (InvalidOperation, OverflowError):
            return None


@dataclass(frozen=True, slots=True)
class MatchResult:
    closures: list[Closure]
    open_lots: list[OpenLot]
    #: Charges from sales that matched no lot at all -- the buys predate the export
    #: window, so there is nothing to attribute them to. They are collected rather
    #: than discarded because `rebuild()` counts them toward the attributed side of
    #: `Sum(attributed) == Sum(ledger)`; dropped, a single such sale would make
    #: attributed fall short and refuse every method for the entire ledger.
    unmatched_charges: Charges = field(default_factory=Charges.zero)

    @property
    def quantity(self) -> Decimal:
        return sum((lot.quantity for lot in self.open_lots), Decimal(0))

    @property
    def cost_basis(self) -> Decimal:
        return sum((lot.cost_basis for lot in self.open_lots), Decimal(0))

    @property
    def charges(self) -> Charges:
        return sum((lot.charges for lot in self.open_lots), Charges.zero())

    @property
    def realised(self) -> Decimal:
        return sum((closure.pnl for closure in self.closures), Decimal(0))


@dataclass(slots=True)
class _WorkingLot:
    """A lot mid-match. Remembers the quantity it was bought with, so charges can be
    apportioned against the original rather than the dwindling balance."""

    transaction: LotTransaction
    remaining: Decimal
    #: Index into the match list for each closure drawn from this lot.
    closure_indices: list[int] = field(default_factory=list)
    #: Quantity taken by each of those closures, in the same order.
    consumed: list[Decimal] = field(default_factory=list)


@dataclass(slots=True)
class _Match:
    lot: LotTransaction
    sale: LotTransaction
    quantity: Decimal


def _select(available: list[_WorkingLot], method: LotMethod) -> _WorkingLot:
    if method == "FIFO":
        return available[0]
    if method == "LIFO":
        return available[-1]
    # HIFO: dearest first, which realises the smallest gain or the largest loss.
    return max(available, key=lambda lot: lot.transaction.price)


def match_lots(transactions: list[LotTransaction], method: LotMethod) -> MatchResult:
    """Match one instrument's transactions into open lots and closures.

    Transactions must be in chronological order; a SELL cannot match a BUY the
    matcher has not seen yet. Passing them unordered raises rather than silently
    producing a plausible, wrong cost basis.
    """
    previous: date | None = None
    for txn in transactions:
        if previous is not None and txn.trade_date < previous:
            raise ValueError(
                f"transactions must be chronological; {txn.id} at {txn.trade_date} "
                f"follows {previous}"
            )
        previous = txn.trade_date

    working: list[_WorkingLot] = []
    matches: list[_Match] = []
    #: Closure indices produced by each sale, so its charges can be split across them.
    sale_closures: list[tuple[LotTransaction, list[int]]] = []

    for txn in transactions:
        if txn.side == "BUY":
            working.append(_WorkingLot(transaction=txn, remaining=txn.quantity))
            continue

        needed = txn.quantity
        produced: list[int] = []
        while needed > 0:
            available = [lot for lot in working if lot.remaining > 0]
            if not available:
                # Selling more than is held is a short, which this ledger does not
                # model. The excess is dropped rather than invented into a negative
                # lot -- and the loop must stop, not spin.
                break
            picked = _select(available, method)
            taken = min(picked.remaining, needed)
            picked.remaining -= taken
            needed -= taken

            index = len(matches)
            matches.append(_Match(lot=picked.transaction, sale=txn, quantity=taken))
            picked.closure_indices.append(index)
            picked.consumed.append(taken)
            produced.append(index)

        sale_closures.append((txn, produced))

    # ── Charge attribution, second pass.
    #
    # Each transaction's charges are split across exactly the things it touched: a
    # buy across its closures plus whatever of it is still open, a sale across the
    # closures it produced. Every charge is therefore accounted for exactly once,
    # and `apportion_charges` guarantees the parts sum to the whole.
    closure_charges = [Charges.zero()] * len(matches)
    open_lots: list[OpenLot] = []

    for lot in working:
        weights = [*lot.consumed, lot.remaining]
        shares = apportion_charges(lot.transaction.charges, weights)
        for index, share in zip(lot.closure_indices, shares, strict=False):
            closure_charges[index] += share
        if lot.remaining > 0:
            open_lots.append(
                OpenLot(
                    id=lot.transaction.id,
                    opened_on=lot.transaction.trade_date,
                    quantity=lot.remaining,
                    price=lot.transaction.price,
                    charges=shares[-1],
                )
            )

    unmatched = Charges.zero()
    for sale, indices in sale_closures:
        if not indices:
            # A sale that matched no lot at all -- its buys predate the export
            # window, or the position was already exhausted -- still cost what it
            # cost. There is no closure to carry those charges, so they go into
            # `unmatched_charges` and `rebuild()` counts them toward the attributed
            # total. Dropping them here would make attributed fall short of the
            # ledger and refuse every method for the whole ledger, with a message
            # blaming apportionment.
            unmatched += sale.charges
            continue
        shares = apportion_charges(sale.charges, [matches[i].quantity for i in indices])
        for index, share in zip(indices, shares, strict=False):
            closure_charges[index] += share

    closures = [
        Closure(
            lot_id=match.lot.id,
            sale_id=match.sale.id,
            opened_on=match.lot.trade_date,
            open_price=match.lot.price,
            closed_on=match.sale.trade_date,
            close_price=match.sale.price,
            quantity=match.quantity,
            charges=closure_charges[i],
        )
        for i, match in enumerate(matches)
    ]

    return MatchResult(
        closures=closures, open_lots=open_lots, unmatched_charges=unmatched
    )

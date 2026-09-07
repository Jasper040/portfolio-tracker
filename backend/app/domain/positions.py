"""The daily share count and the daily cash balance. Pure.

This is the half of the portfolio-value chart that the ledger can prove. A price
is a fact about the world; how many shares were held on a Tuesday is a fact about
the account, and this module derives it from nothing else.

Two series come out of one pass because they come from one pass of the same rows,
and separating them would mean reading the ledger twice to answer one question.

The rule for both is **"on or before"**: the state on day D reflects every row
dated D or earlier. That is what makes a Saturday transaction land on Monday
rather than disappear, and it is the same rule the valuation join uses for prices
-- so carry-forward is not separate machinery anywhere in M2, it is what "latest
on or before" means.

Share counts are post-split. `apply_splits` restates the fills that predate a
split rather than inserting an event on the split date, so the count is in
today's shares on every day of the series -- which is what makes the chart
continuous across a split instead of stepping by a factor of ten.

Purity is not decoration here. `through` is a parameter, never `date.today()`,
which is what lets `rebuild()` write `position_daily` and still be a function of
its inputs alone.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Protocol

from app.domain.lots import LotTransaction
from app.domain.orders import LedgerRow, to_lot_transactions
from app.domain.splits import apply_splits, derive_splits

_ZERO = Decimal("0.00")
_SATURDAY = 5

class LedgerCashRow(LedgerRow, Protocol):
    """`LedgerRow` plus the one field the cash series needs.

    Extending the Protocol rather than restating it: the share side of this
    module hands its rows straight to `to_lot_transactions`, so the two views of
    a row must not be allowed to drift apart. `_moves_euros` below needs no
    field beyond `net_base` -- `txn_type` and `order_ref` are already declared
    on `LedgerRow` itself.
    """

    net_base: Decimal

#: DeGiro's own wording for a currency-conversion journal entry (`Valuta
#: Creditering`/`Valuta Debitering`), as classified by `app.ingest.degiro.
#: account_csv`. Restated here as a plain string rather than imported: `domain/`
#: takes no dependency on `ingest/`, in either direction, so a row is judged by
#: the shape the ledger already gives it (Sec 4.1).
_FX_CONVERT = "FX_CONVERT"

def _moves_euros(row: LedgerCashRow) -> bool:
    """Whether `row.net_base` is a euro cash movement that should be counted --
    once.

    Measured empirically against `Portfolio.csv`'s own stated cash line (Sec
    5.4), not assumed: three candidates were tried against the owner's real
    export first, each scored by its euro ERROR against the broker's own
    balance (a delta, not a balance -- it carries no figure of the owner's).

    * Sum every row's `net_base`, unmodified: error **-6915.52**. A DeGiro
      "Valuta Creditering"/"Valuta Debitering" pair that SETTLES a
      foreign-currency trade carries that same trade's own `order_ref`, and its
      euro leg restates the exact principal-plus-autoFX the trade's own
      `net_base` (`Total EUR`) already carries. Summing both counts it twice.
    * Exclude a trade's own `net_base` whenever its price is not in EUR: error
      **+116.01**. This removes the duplicate, but it throws the whole trade
      away rather than just the duplicated part, taking its commission
      (`fee_base`) down with it -- and the commission has no other row to live
      in, because `account_csv.py` already drops the matching "DEGIRO
      Transactiekosten en/of" row as a duplicate of the trade's own fee column.
    * Keep every trade's `net_base`; exclude every `FX_CONVERT` row outright:
      error **-66.82**. A `Valuta Creditering`/`Debitering` pair with no
      `order_ref` is not settling a trade at all -- it is converting a
      dividend, interest, tax or securities-lending receipt into euros, and it
      is the ONLY row in the whole ledger that records that movement.
      Excluding it loses real income that nothing else restates.

    The field that actually tells the two apart is `order_ref`, not currency
    and not the description text -- both "Valuta Creditering" and "Valuta
    Debitering" wording is used for both cases, so no value heuristic on the
    row itself can separate them. A conversion that SETTLES a trade carries
    THAT TRADE's own `order_ref`; a conversion of investment income carries
    none, because there is no order to attach it to. Excluding only the
    order-linked `FX_CONVERT` rows reconciles to **+0.01** -- inside the one-cent
    rounding the export already carries on a handful of its own trade rows.

    This is the same shape as the cash-sweep trap in Sec 3.3 and the
    corporate-action detection in Sec 3.4: a row that looks exactly like an
    independent cash flow while actually being the mechanics of one already
    counted elsewhere, told apart by a FIELD rather than a value heuristic --
    which is what lets the rule survive an export whose amounts change from one
    export to the next. **Do not simplify this back to "sum every row."** It is
    also this predicate, not the aggregate sum, that
    `test_lands_on_the_brokers_own_cash_balance` actually guards: an export
    that stopped tagging settlement conversions with an `order_ref` would turn
    that test red rather than mis-summing quietly.

    **A known limitation, not a bug.** A foreign-currency trade bought from a
    PRE-EXISTING foreign-currency balance -- rather than freshly converted from
    EUR for the purpose -- carries no order-linked `FX_CONVERT` row at all,
    because no conversion happened for it. Such a trade is NOT excluded by the
    rule above: it is an ordinary trade row, so its `net_base` (`Total EUR`) is
    COUNTED like any other trade's -- an earlier note in this project's own
    working log described this shape as having its euro cost "dropped
    entirely," which is the REJECTED currency-based-exclusion candidate's
    behaviour (the second bullet above), not this one. Counting it here deducts
    a euro-equivalent amount from `balance_base` for a transaction that never
    actually spent EUR -- the money that moved was the pre-existing foreign
    balance, not the EUR one, and that foreign balance is structurally
    invisible to this module either way: `CashPoint` and `cash_daily` carry
    only `balance_base`, the EUR balance, with no ledger of any other
    currency's cash. Spec-compliant as implemented, and it has not occurred in
    the owner's real export, so it is recorded here as a known limitation
    rather than fixed.
    """
    return not (row.txn_type == _FX_CONVERT and row.order_ref is not None)

@dataclass(frozen=True, slots=True)
class PositionPoint:
    on: date
    isin: str
    quantity: Decimal

@dataclass(frozen=True, slots=True)
class CashPoint:
    on: date
    balance_base: Decimal

@dataclass(frozen=True, slots=True)
class DailySeries:
    """Everything the ledger knows about a day, before any price is applied."""

    positions: tuple[PositionPoint, ...]
    cash: tuple[CashPoint, ...]

    @property
    def first_position_day(self) -> date | None:
        """Where the value chart starts (M2-7).

        Not where the cache starts and not where the cash series starts. Valuing
        days on which nothing was held would draw a flat line at the cash balance
        for however long the account sat empty, and invite the reader to wonder
        what broke.
        """
        return self.positions[0].on if self.positions else None

def weekdays(start: date, end: date) -> Iterator[date]:
    """Every Monday-to-Friday from `start` to `end`, both inclusive.

    M2-3: every weekday is valued, with no holes. Weekends are not holes -- no
    venue was open and no reader expects a point -- but a closed venue on a
    Tuesday is, which is why the staleness of a carried-forward price is recorded
    rather than smoothed away.
    """
    day = start
    while day <= end:
        if day.weekday() < _SATURDAY:
            yield day
        day += timedelta(days=1)

def _fills_by_isin(rows: Sequence[LedgerCashRow]) -> dict[str, list[LotTransaction]]:
    """Split-adjusted fills per instrument, chronological.

    Borrowed wholesale from M1 rather than re-derived: `to_lot_transactions`
    already decides which rows are share movements and `apply_splits` already
    restates the pre-split ones. A second implementation here would be free to
    disagree with the one that produced `lot`, and the two would then report
    different share counts for the same day with nothing to say which was right.
    """
    splits = derive_splits(rows)
    return {
        isin: apply_splits(fills, [s for s in splits if s.isin == isin])
        for isin, fills in to_lot_transactions(rows).items()
    }

def _position_points(
    fills_by_isin: dict[str, list[LotTransaction]], *, start: date, through: date
) -> tuple[PositionPoint, ...]:
    """Running quantity per instrument, stepped once through each fill list.

    Equivalent to recomputing the running total from scratch for every day --
    each day's answer is still "every fill dated on or before this day, summed"
    -- but each fill is visited once per instrument rather than once per
    instrument per day: one linear pass over each instrument's fills, instead
    of a full rescan of them on every day in the window.
    """
    points: list[PositionPoint] = []
    isins = sorted(fills_by_isin)
    running: dict[str, Decimal] = {isin: _ZERO for isin in isins}
    cursor: dict[str, int] = {isin: 0 for isin in isins}
    for day in weekdays(start, through):
        for isin in isins:
            fills = fills_by_isin[isin]
            index = cursor[isin]
            quantity = running[isin]
            while index < len(fills) and fills[index].trade_date <= day:
                fill = fills[index]
                quantity += fill.quantity if fill.side == "BUY" else -fill.quantity
                index += 1
            running[isin] = quantity
            cursor[isin] = index
            if quantity != 0:
                points.append(PositionPoint(on=day, isin=isin, quantity=quantity))
    return tuple(points)

def _cash_points(
    rows: Sequence[LedgerCashRow], *, start: date, through: date
) -> tuple[CashPoint, ...]:
    """A running sum of `net_base` over every row that MOVES euros, once each.

    `net_base` is DeGiro's own `Total EUR` -- what actually hit the cash account
    (Sec 5.4), never recomputed. "Every row" is close to the rule but not quite
    it: a corporate action's two suppressed legs offset to zero, so including
    them changes nothing, but a foreign-currency trade and the `Valuta`
    conversion that settles it both carry the SAME euro movement under two
    different rows, and summing both would double it. `_moves_euros` is the
    line between "every row" and "every row, once" -- see its docstring for the
    three rules that were measured and rejected before this one, and why. This
    is what makes the balance reconcile against the broker's own cash line
    rather than approximately agree with it.
    """
    ordered = sorted(
        (row for row in rows if _moves_euros(row)),
        key=lambda row: (row.trade_date, row.source_ref),
    )
    points: list[CashPoint] = []
    balance = _ZERO
    index = 0
    for day in weekdays(start, through):
        while index < len(ordered) and ordered[index].trade_date <= day:
            balance += ordered[index].net_base
            index += 1
        points.append(CashPoint(on=day, balance_base=balance))
    return tuple(points)

def daily_series(rows: Sequence[LedgerCashRow], *, through: date) -> DailySeries:
    """Daily share counts and cash balances, from the first ledger day to `through`.

    `through` is a parameter and not today's date, so this function has one
    answer for one input on any day it is called.
    """
    if not rows:
        return DailySeries(positions=(), cash=())

    start = min(row.trade_date for row in rows)
    if through < start:
        return DailySeries(positions=(), cash=())

    return DailySeries(
        positions=_position_points(_fills_by_isin(rows), start=start, through=through),
        cash=_cash_points(rows, start=start, through=through),
    )

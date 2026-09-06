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
    a row must not be allowed to drift apart.
    """

    net_base: Decimal

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
    """A running sum of `net_base` over EVERY row, economic or not.

    `net_base` is DeGiro's own `Total EUR` -- what actually hit the cash account
    (Sec 5.4), never recomputed. A corporate action's two suppressed legs offset
    to zero, so including them changes nothing; the point is that the rule is
    "every row", which is what makes this balance reconcile against the broker's
    own cash line rather than approximately agree with it.
    """
    ordered = sorted(rows, key=lambda row: (row.trade_date, row.source_ref))
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

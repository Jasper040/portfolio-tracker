"""External cash flows per day. The one quantity M6a adds to what M2 built.

M6a section 3.2. A time-weighted return removes the money the owner moved across
the account boundary, because a deposit is not a gain. Everything else that
changes the cash balance -- a trade's settlement, a dividend, its withholding, a
fee, interest -- is inside the portfolio and is part of what the return measures.

**Two types, and only two (M6a-6).** `DEPOSIT` and `WITHDRAWAL` are the rows that
cross the account boundary. The Sec 3.3 cash sweeps are absent because they are
not in the ledger at all: `ingest/degiro/account_csv.py` drops them at parse
time, and `tests/integration/test_portfolio_return.py` proves the return series
is identical with and without them. The flatex transfers ARE present -- that
parser types them by sign, because they move money to and from the owner's own
bank, which is outside the pot `cash_daily` sums.

The type names are restated as plain strings rather than imported from the
parser, for the reason `domain/positions.py` gives about `FX_CONVERT`: a ledger
row is judged by the shape it already has, and analytics takes no dependency on
a broker-specific parser module.

`net_base` is the amount, signed as it hit the cash account. It is the column
`cash_daily` is a running sum of, which is what makes subtracting a flow from a
change in value exact rather than approximately right.

This module sums per CALENDAR day. Moving a flow onto the valuation grid -- a
Saturday deposit belongs to Monday's link -- is `portfolio_return.py`'s job,
because only a caller holding the grid knows which close a date falls before.
It names no close column and touches no price table.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from typing import Protocol

from sqlalchemy import Engine
from sqlmodel import Session, col, select

from app.models.ledger import Transaction

#: The rows that cross the account boundary. See the module docstring.
EXTERNAL_FLOW_TYPES: frozenset[str] = frozenset({"DEPOSIT", "WITHDRAWAL"})

_ZERO = Decimal("0.00")


class LedgerFlowRow(Protocol):
    """The three fields a flow needs off a `Transaction`."""

    txn_type: str
    trade_date: date
    net_base: Decimal


def net_flows_by_day(rows: Iterable[LedgerFlowRow]) -> dict[date, Decimal]:
    """Net external flow per calendar day, in date order. Pure.

    A day whose deposit and withdrawal cancel is present with zero rather than
    absent: the ledger recorded flows that day, and dropping the key would make
    "nothing happened" and "two things cancelled" look the same. Neither changes
    a return.
    """
    totals: dict[date, Decimal] = {}
    for row in rows:
        if row.txn_type not in EXTERNAL_FLOW_TYPES:
            continue
        totals[row.trade_date] = totals.get(row.trade_date, _ZERO) + row.net_base
    return dict(sorted(totals.items()))


def external_flows(engine: Engine) -> dict[date, Decimal]:
    """Every external flow the ledger holds, netted per calendar day."""
    with Session(engine) as session:
        rows = session.exec(
            select(Transaction).where(
                col(Transaction.txn_type).in_(sorted(EXTERNAL_FLOW_TYPES))
            )
        ).all()
    return net_flows_by_day(rows)

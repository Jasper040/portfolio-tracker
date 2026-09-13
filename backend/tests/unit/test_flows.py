"""Which ledger rows cross the account boundary, and how they net. No database.

M6a-6: `DEPOSIT` and `WITHDRAWAL` only. Every other type that moves cash -- a
trade, a dividend, its withholding, a fee, interest, a conversion -- is inside
the portfolio, and a time-weighted return exists to measure it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal as D

from app.analytics.flows import EXTERNAL_FLOW_TYPES, net_flows_by_day

MON = date(2025, 3, 3)
TUE = date(2025, 3, 4)
WED = date(2025, 3, 5)


@dataclass(frozen=True)
class Row:
    txn_type: str
    trade_date: date
    net_base: D
    settle_date: date | None = None


def test_a_deposit_is_a_positive_flow_and_a_withdrawal_a_negative_one() -> None:
    rows = [Row("DEPOSIT", MON, D("1000.00")), Row("WITHDRAWAL", WED, D("-250.00"))]
    assert net_flows_by_day(rows) == {MON: D("1000.00"), WED: D("-250.00")}


def test_a_flow_is_keyed_by_its_value_date() -> None:
    """Money the broker made spendable on Monday and booked on Tuesday is Monday's
    flow. `cash_daily` holds it from Monday, and a flow keyed a day after its cash
    would be subtracted from a link the money was already in."""
    rows = [
        Row("DEPOSIT", TUE, D("1000.00"), settle_date=MON),
        Row("WITHDRAWAL", WED, D("-250.00"), settle_date=TUE),
    ]
    assert net_flows_by_day(rows) == {MON: D("1000.00"), TUE: D("-250.00")}


def test_a_flow_with_no_value_date_falls_back_to_its_trade_date() -> None:
    rows = [Row("DEPOSIT", TUE, D("1000.00"), settle_date=None)]
    assert net_flows_by_day(rows) == {TUE: D("1000.00")}


def test_nothing_else_that_moves_cash_is_a_flow() -> None:
    """Every other type the ledger holds. Each changes `V`; none crosses the boundary."""
    rows = [
        Row(txn_type, MON, D("12.34"))
        for txn_type in (
            "BUY", "SELL", "DIVIDEND", "DIVIDEND_TAX", "FEE", "TAX", "INTEREST",
            "SECURITIES_LENDING", "FX_CONVERT", "CORPORATE_ACTION",
        )
    ]
    assert net_flows_by_day(rows) == {}


def test_the_boundary_is_exactly_two_types() -> None:
    assert EXTERNAL_FLOW_TYPES == {"DEPOSIT", "WITHDRAWAL"}


def test_flows_on_one_day_net_and_the_day_stays_present() -> None:
    """Two flows that cancel are still two flows that happened. Neither changes a
    return; dropping the key would make the mapping claim nothing happened."""
    rows = [Row("WITHDRAWAL", TUE, D("-800.00")), Row("DEPOSIT", TUE, D("800.00"))]
    assert net_flows_by_day(rows) == {TUE: D("0.00")}


def test_days_come_out_in_date_order() -> None:
    rows = [Row("DEPOSIT", WED, D("1.00")), Row("DEPOSIT", MON, D("1.00"))]
    assert list(net_flows_by_day(rows)) == [MON, WED]

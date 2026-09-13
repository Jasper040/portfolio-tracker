"""`external_flows` against a real table: which rows it reads, and nothing else."""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

from app.analytics.flows import external_flows
from app.db import create_engine_and_tables
from tests.integration.synthetic_ledger import Ledger

MON = date(2025, 3, 3)
TUE = date(2025, 3, 4)
WED = date(2025, 3, 5)
FRI = date(2025, 3, 7)


def test_reads_only_the_rows_that_cross_the_account_boundary() -> None:
    engine = (
        Ledger()
        .deposit(MON, "1000.00")
        .buy(TUE, "10", "20.00", fee="2.00")
        .row(WED, "DIVIDEND", "5.00")
        .row(WED, "DIVIDEND_TAX", "-0.75")
        .withdraw(FRI, "100.00")
        .engine
    )
    assert external_flows(engine) == {MON: D("1000.00"), FRI: D("-100.00")}


def test_an_empty_ledger_has_no_flows() -> None:
    assert external_flows(create_engine_and_tables("sqlite://")) == {}

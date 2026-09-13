"""A hand-written ledger for the M6a tests: deposits, trades and closes.

Not a test module. Built on the real tables and driven through the real
`rebuild()`, because M6a's claims about trades -- that a buy at the close leaves
the day's return untouched -- are claims about how `cash_daily` and
`position_daily` are DERIVED from a fill's `net_base`. Seeding those two tables
by hand, as `test_valuation.py` does, would test the arithmetic against the
author's belief about the derivation rather than against the derivation.

Same shape as the `Seed` builders elsewhere in this directory: each call commits
and returns the builder. Every ISIN and amount is invented.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import Engine
from sqlmodel import Session

from app.analytics.rebuild import rebuild
from app.db import create_engine_and_tables
from app.models.ledger import Account, ImportBatch, Transaction
from app.models.market import BenchmarkDaily, FxDaily, PriceDaily

D = Decimal
ZERO = D("0.00")
FETCHED = datetime(2026, 9, 6, 12, 0, 0)

ISIN = "XX0000000061"  # invented; no holding of anyone's
OTHER = "XX0000000062"  # invented; no holding of anyone's


class Ledger:
    def __init__(self) -> None:
        self._engine = create_engine_and_tables("sqlite://")
        self._account_id = uuid4()
        self._batch_id = uuid4()
        with Session(self._engine) as session:
            session.add(
                Account(
                    id=self._account_id, broker="degiro", name="synthetic",
                    base_currency="EUR",
                )
            )
            session.add(
                ImportBatch(
                    id=self._batch_id, source="degiro", filename="synthetic",
                    file_sha256="0" * 64, parser_version="1", imported_at=FETCHED,
                    row_count=0, inserted_count=0,
                )
            )
            session.commit()

    @property
    def engine(self) -> Engine:
        return self._engine

    def _add(self, **fields: object) -> "Ledger":
        with Session(self._engine) as session:
            session.add(
                Transaction(
                    id=uuid4(),
                    account_id=self._account_id,
                    import_batch_id=self._batch_id,
                    source="degiro",
                    source_ref=f"synthetic-{uuid4()}",
                    raw_json="{}",
                    **fields,
                )
            )
            session.commit()
        return self

    def row(
        self, on: date, txn_type: str, amount: str, *, value_date: date | None = None
    ) -> "Ledger":
        """A cash-only row: a dividend, a fee, a flow. `amount` is signed as it
        hit the cash account. `on` is the booking date; `value_date`, when given,
        is the broker's value date and is stored as `settle_date`."""
        return self._add(
            txn_type=txn_type, trade_date=on, settle_date=value_date, fee_base=ZERO,
            tax_base=ZERO, net_base=D(amount),
        )

    def deposit(self, on: date, amount: str, *, value_date: date | None = None) -> "Ledger":
        return self.row(on, "DEPOSIT", amount, value_date=value_date)

    def withdraw(self, on: date, amount: str, *, value_date: date | None = None) -> "Ledger":
        """`amount` is written positive and booked negative, as the broker does."""
        return self.row(on, "WITHDRAWAL", str(-D(amount)), value_date=value_date)

    def buy(
        self, on: date, quantity: str, price: str, *, fee: str = "0.00", isin: str = ISIN
    ) -> "Ledger":
        """One fill. `value_base` is what the shares cost and `fee_base` a debit,
        both negative; `net_base` is their sum -- what left the cash account."""
        cost = -(D(quantity) * D(price))
        charge = -D(fee)
        return self._add(
            txn_type="BUY", trade_date=on, trade_time="12:00", isin=isin,
            quantity=D(quantity), price_local=D(price), currency_local="EUR",
            value_base=cost, fee_base=charge, tax_base=ZERO, autofx_fee_base=ZERO,
            net_base=cost + charge, order_ref=f"order-{uuid4()}",
        )

    def close(self, on: date, price: str, *, isin: str = ISIN) -> "Ledger":
        """One unadjusted close. The adjusted close is deliberately different, so
        a reader taking the wrong column produces a wrong number."""
        with Session(self._engine) as session:
            session.add(
                PriceDaily(
                    id=uuid4(), isin=isin, price_date=on, close_unadjusted=D(price),
                    close_adjusted=D(price) / 2, currency="EUR", source="yahoo",
                    fetched_at=FETCHED,
                )
            )
            session.commit()
        return self

    def benchmark(
        self, on: date, close: str, *, key: str = "world", currency: str = "EUR"
    ) -> "Ledger":
        """One benchmark close. The unadjusted close is a flat 100.00 on every
        day, so a reader taking the wrong column reports a benchmark that went
        nowhere rather than the same number."""
        with Session(self._engine) as session:
            session.add(
                BenchmarkDaily(
                    id=uuid4(), key=key, price_date=on, close_unadjusted=D("100.00"),
                    close_adjusted=D(close), currency=currency, source="yahoo",
                    fetched_at=FETCHED,
                )
            )
            session.commit()
        return self

    def rate(self, on: date, from_ccy: str, value: str) -> "Ledger":
        """Units of `from_ccy` per 1 EUR -- divide by it to reach EUR."""
        with Session(self._engine) as session:
            session.add(
                FxDaily(
                    id=uuid4(), from_ccy=from_ccy, to_ccy="EUR", rate_date=on,
                    rate=D(value), source="ecb", fetched_at=FETCHED,
                )
            )
            session.commit()
        return self

    def rebuilt(self, through: date) -> Engine:
        """Derive `position_daily` and `cash_daily` the way the app does."""
        rebuild(self._engine, "FIFO", through=through)
        return self._engine

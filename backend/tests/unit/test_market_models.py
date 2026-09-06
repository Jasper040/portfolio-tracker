"""The cache tables, and the two properties that make them a cache.

`price_daily` and `fx_daily` hold what the network said and when it said it. Two
things have to be true of them and are asserted here rather than assumed:

* money round-trips as an exact Decimal, so a close of 12.30 comes back as
  "12.30" and not as 12.299999999999999;
* the same (instrument, day) or (currency pair, day) cannot be stored twice,
  because a second row would make "the price on that day" a question with two
  answers and the join would pick one arbitrarily.

The FX direction is asserted too. `rate` is units of `from_ccy` per 1 unit of
`to_ccy` -- the same direction as `domain.money.FxRate` and as DeGiro's own
`Exchange rate` column, which means you DIVIDE by it to reach the base currency.
Storing the other direction would produce numbers that are wrong and entirely
plausible, which is the failure `FxRate` exists to make impossible.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.db import create_engine_and_tables
from app.models.ledger import CashDaily, PositionDaily
from app.models.market import FxDaily, PriceDaily, SymbolReview

D = Decimal
FETCHED = datetime(2026, 9, 6, 12, 0, 0)

@pytest.fixture(name="engine")
def _engine() -> Engine:
    return create_engine_and_tables("sqlite://")

def _price(**overrides) -> PriceDaily:
    fields = dict(
        id=uuid4(),
        isin="NL0000000001",
        price_date=date(2025, 3, 3),
        close_unadjusted=D("12.30"),
        close_adjusted=D("11.80"),
        currency="EUR",
        source="yahoo",
        fetched_at=FETCHED,
    )
    fields.update(overrides)
    return PriceDaily(**fields)

class TestPriceDaily:
    def test_stores_both_closes_as_exact_decimals(self, engine) -> None:
        with Session(engine) as session:
            session.add(_price())
            session.commit()
        with Session(engine) as session:
            row = session.exec(select(PriceDaily)).one()
        assert row.close_unadjusted == D("12.30")
        assert str(row.close_unadjusted) == "12.30"
        assert row.close_adjusted == D("11.80")

    def test_refuses_a_second_price_for_the_same_instrument_and_day(self, engine) -> None:
        with Session(engine) as session:
            session.add(_price())
            session.commit()
        with Session(engine) as session, pytest.raises(IntegrityError):
            session.add(_price(close_unadjusted=D("99.00")))
            session.commit()

    def test_records_who_supplied_it_and_when(self, engine) -> None:
        """Provenance is not decoration: `coverage: "manual"` is only a fact
        because the row says which provider answered."""
        with Session(engine) as session:
            session.add(_price(source="manual"))
            session.commit()
        with Session(engine) as session:
            row = session.exec(select(PriceDaily)).one()
        assert row.source == "manual"
        assert row.fetched_at == FETCHED

class TestFxDaily:
    def test_stores_the_currency_pair_explicitly(self, engine) -> None:
        """A rate without a stated direction is a runtime error waiting to be
        plausible (parent doc Sec 5.3)."""
        with Session(engine) as session:
            session.add(
                FxDaily(
                    id=uuid4(),
                    from_ccy="USD",
                    to_ccy="EUR",
                    rate_date=date(2025, 3, 3),
                    rate=D("1.0854"),
                    source="ecb",
                    fetched_at=FETCHED,
                )
            )
            session.commit()
        with Session(engine) as session:
            row = session.exec(select(FxDaily)).one()
        assert (row.from_ccy, row.to_ccy) == ("USD", "EUR")
        assert row.rate == D("1.0854")

    def test_the_stored_rate_is_the_one_FxRate_divides_by(self, engine) -> None:
        """The direction contract, asserted against a persisted row, not a literal.

        1.0854 USD per 1 EUR means 108.54 USD is 100.00 EUR. If the reciprocal
        were stored, this would come out as 117.81 EUR -- wrong by 18% and
        entirely believable. The end-to-end direction guarantee is completed by
        Task 5 (the ECB provider's own parse-direction test) and Task 9 (the
        join, where multiplying instead of dividing yields 216.32 rather than
        200.00); this test's job is to pin that a stored row's three columns
        feed `FxRate` in that order.
        """
        from app.domain.money import FxRate, Money

        with Session(engine) as session:
            session.add(
                FxDaily(
                    id=uuid4(),
                    from_ccy="USD",
                    to_ccy="EUR",
                    rate_date=date(2025, 3, 3),
                    rate=D("1.0854"),
                    source="ecb",
                    fetched_at=FETCHED,
                )
            )
            session.commit()

        with Session(engine) as session:
            stored = session.exec(select(FxDaily)).one()

        rate = FxRate(
            from_currency=stored.from_ccy,
            to_currency=stored.to_ccy,
            rate=stored.rate,
            as_of=stored.rate_date,
        )
        converted = rate.convert(Money(D("108.54"), "USD"))
        assert converted.currency == "EUR"
        assert converted.amount == D("100")

    def test_refuses_a_second_rate_for_the_same_pair_and_day(self, engine) -> None:
        def row(rate: str) -> FxDaily:
            return FxDaily(
                id=uuid4(),
                from_ccy="USD",
                to_ccy="EUR",
                rate_date=date(2025, 3, 3),
                rate=D(rate),
                source="ecb",
                fetched_at=FETCHED,
            )

        with Session(engine) as session:
            session.add(row("1.0854"))
            session.commit()
        with Session(engine) as session, pytest.raises(IntegrityError):
            session.add(row("1.2000"))
            session.commit()

class TestDerivedDailyTables:
    def test_position_daily_has_no_method_column(self, engine) -> None:
        """M1 proved share counts are method-independent and asserts it in
        `test_every_method_holds_the_same_shares`. A `method` column here would
        invite three copies of one answer, and the day they disagreed there
        would be no way to say which was right."""
        assert "method" not in PositionDaily.model_fields

    def test_position_daily_refuses_two_quantities_for_one_instrument_day(self, engine) -> None:
        def row(quantity: str) -> PositionDaily:
            return PositionDaily(
                id=uuid4(),
                position_date=date(2025, 3, 3),
                isin="NL0000000001",
                quantity=D(quantity),
            )

        with Session(engine) as session:
            session.add(row("10"))
            session.commit()
        with Session(engine) as session, pytest.raises(IntegrityError):
            session.add(row("20"))
            session.commit()

    def test_cash_daily_holds_one_balance_per_day(self, engine) -> None:
        def row(balance: str) -> CashDaily:
            return CashDaily(id=uuid4(), cash_date=date(2025, 3, 3), balance_base=D(balance))

        with Session(engine) as session:
            session.add(row("-100.00"))
            session.commit()
        with Session(engine) as session:
            stored = session.exec(select(CashDaily)).one()
        assert stored.balance_base == D("-100.00")
        with Session(engine) as session, pytest.raises(IntegrityError):
            session.add(row("50.00"))
            session.commit()

    def test_cash_balance_may_be_negative(self, engine) -> None:
        """The account runs a debit balance and pays interest on it (parent doc
        Sec 3.5). A schema that could not hold that would make the net portfolio
        value wrong by the size of the overdraft."""
        with Session(engine) as session:
            session.add(
                CashDaily(id=uuid4(), cash_date=date(2025, 4, 4), balance_base=D("-2000.00"))
            )
            session.commit()
        with Session(engine) as session:
            assert session.exec(select(CashDaily)).one().balance_base < 0

class TestSymbolReview:
    def test_holds_one_open_question_per_instrument(self, engine) -> None:
        """A projection, like `corporate_action_review`: one row per instrument
        still unanswered, rebuilt each run rather than accumulated."""
        def row(candidates: str) -> SymbolReview:
            return SymbolReview(
                id=uuid4(),
                isin="NL0000000001",
                product_name="Example Holdings",
                trade_currency="EUR",
                candidates=candidates,
                detected_at=FETCHED,
            )

        with Session(engine) as session:
            session.add(row('[{"symbol": "EXA.AS"}]'))
            session.commit()
        with Session(engine) as session, pytest.raises(IntegrityError):
            session.add(row('[{"symbol": "EXB.DE"}]'))
            session.commit()

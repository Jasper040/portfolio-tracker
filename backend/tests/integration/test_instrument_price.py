"""The price side of the instrument chart (M3 section 5.2).

Reads the unadjusted close only. If this module ever names `close_adjusted`,
`test_no_double_count.py` fails and it is right to.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal as D
from uuid import uuid4

import pytest
from sqlmodel import Session, select

from app.analytics.instrument_price import instrument_price_view
from app.db import create_engine_and_tables
from app.domain.positions import weekdays
from app.models.ledger import Account, ImportBatch, PositionDaily, Transaction
from app.models.market import FxDaily, PriceDaily

ISIN = "XX0000000001"  # invented; no holding of anyone's

ZERO = D("0.00")
FETCHED = datetime(2026, 9, 6, 12, 0, 0)

WINDOW_START = date(2026, 1, 1)
WINDOW_END = date(2026, 3, 31)

RUN_ONE_START = date(2026, 1, 1)
RUN_ONE_END = date(2026, 1, 8)
SELL_ON = date(2026, 1, 9)
RUN_TWO_START = date(2026, 3, 2)


class Seed:
    """A tiny world: whichever rows a test needs, and nothing else.

    Modeled on `backend/tests/integration/test_valuation.py:57`.
    """

    def __init__(self, engine) -> None:
        self.engine = engine
        with Session(engine) as session:
            session.add(
                Account(id=uuid4(), broker="degiro", name="test", base_currency="EUR")
            )
            session.commit()

    def held(self, on: date, isin: str, quantity: str) -> "Seed":
        with Session(self.engine) as session:
            session.add(
                PositionDaily(
                    id=uuid4(), position_date=on, isin=isin, quantity=D(quantity)
                )
            )
            session.commit()
        return self

    def held_over(self, isin: str, quantity: str, start: date, end: date) -> "Seed":
        for day in weekdays(start, end):
            self.held(day, isin, quantity)
        return self

    def priced(
        self,
        on: date,
        isin: str,
        close: str,
        *,
        currency: str = "EUR",
        source: str = "yahoo",
    ) -> "Seed":
        with Session(self.engine) as session:
            session.add(
                PriceDaily(
                    id=uuid4(),
                    isin=isin,
                    price_date=on,
                    close_unadjusted=D(close),
                    # Deliberately different, so a join that read the wrong
                    # column would produce a wrong number rather than the same one.
                    close_adjusted=D(close) / 2,
                    currency=currency,
                    source=source,
                    fetched_at=FETCHED,
                )
            )
            session.commit()
        return self

    def rate(self, on: date, from_ccy: str, value: str) -> "Seed":
        with Session(self.engine) as session:
            session.add(
                FxDaily(
                    id=uuid4(),
                    from_ccy=from_ccy,
                    to_ccy="EUR",
                    rate_date=on,
                    rate=D(value),
                    source="ecb",
                    fetched_at=FETCHED,
                )
            )
            session.commit()
        return self

    def traded(
        self,
        on: date,
        isin: str,
        txn_type: str,
        quantity: str,
        price: str,
        *,
        currency: str = "EUR",
        fx_rate: str | None = None,
        fee: str = "0.00",
        is_economic: bool = True,
    ) -> "Seed":
        batch_id = uuid4()
        with Session(self.engine) as session:
            session.add(
                ImportBatch(
                    id=batch_id, source="degiro", filename="t.csv", file_sha256="0" * 64,
                    parser_version="1", imported_at=FETCHED, row_count=1, inserted_count=1,
                )
            )
            account = session.exec(select(Account)).first()
            account_id = account.id  # type: ignore[union-attr]
            net = D(quantity) * D(price) * D("-1")
            session.add(
                Transaction(
                    id=uuid4(), account_id=account_id, import_batch_id=batch_id,
                    source="degiro", source_ref=f"t-{uuid4()}", txn_type=txn_type,
                    trade_date=on, isin=isin, product_name="Test Instrument",
                    quantity=D(quantity), price_local=D(price), currency_local=currency,
                    fx_rate=D(fx_rate) if fx_rate is not None else None,
                    fee_base=D(fee), tax_base=ZERO, autofx_fee_base=ZERO,
                    value_base=net, net_base=net, raw_json="{}",
                    is_economic=is_economic,
                )
            )
            session.commit()
        return self


@pytest.fixture(name="seeded_engine")
def _seeded_engine():
    """Bought, sold, re-bought: three intervals, the shape the real export has
    eight instances of. Two priced days bracket the first held run so the
    first interval's return is exactly 100.00 -> 110.00, and every later day
    carries that same price forward (no cutoff on distance), so every point
    in the window is priced."""
    engine = create_engine_and_tables("sqlite://")
    seed = Seed(engine)
    seed.priced(RUN_ONE_START, ISIN, "100.00")
    seed.priced(RUN_ONE_END, ISIN, "110.00")
    seed.held_over(ISIN, "10", RUN_ONE_START, RUN_ONE_END)
    seed.held_over(ISIN, "5", RUN_TWO_START, WINDOW_END)
    seed.traded(RUN_ONE_START, ISIN, "BUY", "10", "100.00")
    seed.traded(SELL_ON, ISIN, "SELL", "-10", "110.00")
    seed.traded(RUN_TWO_START, ISIN, "BUY", "5", "120.00")
    return engine


@pytest.fixture(name="seeded_engine_usd")
def _seeded_engine_usd():
    """A single USD quote, convertible to EUR at a known rate."""
    engine = create_engine_and_tables("sqlite://")
    Seed(engine).priced(date(2026, 1, 5), ISIN, "110.00", currency="USD").rate(
        date(2026, 1, 5), "USD", "1.10"
    )
    return engine


@pytest.fixture(name="seeded_engine_gap")
def _seeded_engine_gap():
    """A price series that starts mid-window: the first day has nothing to
    carry forward from and must be null, not zero."""
    engine = create_engine_and_tables("sqlite://")
    Seed(engine).priced(date(2026, 1, 2), ISIN, "100.00")
    return engine


def test_a_flat_gap_produces_three_intervals(seeded_engine) -> None:
    """Bought, sold, re-bought: in, out, in. Parent doc Sec 7.7, and the shape
    the real export has eight instances of."""
    view = instrument_price_view(
        seeded_engine, ISIN, start=date(2026, 1, 1), end=date(2026, 3, 31)
    )
    assert [i.in_market for i in view.intervals] == [True, False, True]


def test_an_interval_return_is_measured_end_to_end_within_it(seeded_engine) -> None:
    view = instrument_price_view(
        seeded_engine, ISIN, start=date(2026, 1, 1), end=date(2026, 3, 31)
    )
    first = view.intervals[0]
    assert first.price_return == D("0.10")  # 100.00 -> 110.00


def test_held_days_are_marked_and_flat_days_are_not(seeded_engine) -> None:
    """The line is drawn in two registers off this flag. A day the position was
    zero is still priced -- it is the stretch the reader stops watching, and the
    chart refuses to let it disappear."""
    view = instrument_price_view(
        seeded_engine, ISIN, start=date(2026, 1, 1), end=date(2026, 3, 31)
    )
    assert any(p.held for p in view.points)
    assert any(not p.held for p in view.points)
    assert all(p.close_base is not None for p in view.points)


def test_a_marker_per_ledger_transaction_carrying_the_position_after(seeded_engine) -> None:
    view = instrument_price_view(
        seeded_engine, ISIN, start=date(2026, 1, 1), end=date(2026, 3, 31)
    )
    assert [m.side for m in view.markers] == ["BUY", "SELL", "BUY"]
    assert [m.position_after for m in view.markers] == [D("10"), D("0"), D("5")]


def test_a_foreign_quote_is_converted_to_base(seeded_engine_usd) -> None:
    """The price line is in base currency, like everything else on the screen."""
    view = instrument_price_view(
        seeded_engine_usd, ISIN, start=date(2026, 1, 1), end=date(2026, 1, 5)
    )
    # 110.00 USD at 1.10 USD per EUR. `rate` is units of quote per 1 base, so
    # divide -- the same direction as domain.money.FxRate.
    assert view.points[-1].close_base == D("100.00")


def test_an_unpriceable_day_is_null_and_not_zero(seeded_engine_gap) -> None:
    """Parent doc Sec 8.1. A zero is a claim; this is the absence of one."""
    view = instrument_price_view(
        seeded_engine_gap, ISIN, start=date(2026, 1, 1), end=date(2026, 1, 5)
    )
    missing = [p for p in view.points if p.close_base is None]
    assert missing
    assert all(p.coverage == "missing" for p in missing)


def test_the_view_coverage_is_the_worst_day_in_the_window(seeded_engine_gap) -> None:
    view = instrument_price_view(
        seeded_engine_gap, ISIN, start=date(2026, 1, 1), end=date(2026, 1, 5)
    )
    assert view.coverage == "missing"

"""The comparison side (M3 section 5.3). Adjusted close only.

The rebasing test is the load-bearing one: a figure that changes when the
reader changes the range control is not a figure.

Every mistake available in this module produces a plausible number rather than
an error, which is why the fixtures are built to make a wrong answer visibly
wrong: the two closes never hold the same value, and the foreign benchmark
rises by exactly as much as its currency falls, so an implementation that
rebases before it converts reports 10% where the right answer is nothing.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal as D
from uuid import uuid4

import pytest
from sqlmodel import Session, select

from app.analytics.instrument_price import Interval
from app.analytics.instrument_return import comparison
from app.db import create_engine_and_tables
from app.models.ledger import Account
from app.models.market import BenchmarkDaily, FxDaily, PriceDaily

ISIN = "XX0000000001"  # invented; no holding of anyone's
KEY = "world"

FETCHED = datetime(2026, 9, 6, 12, 0, 0)

HELD = Interval(start=date(2026, 1, 5), end=date(2026, 1, 30),
                in_market=True, price_return=D("0.10"))
FLAT = Interval(start=date(2026, 2, 2), end=date(2026, 2, 27),
                in_market=False, price_return=D("0.05"))
HELD_AGAIN = Interval(start=date(2026, 3, 2), end=date(2026, 3, 31),
                      in_market=True, price_return=D("0.04"))

MID_FLAT = date(2026, 2, 16)


class Seed:
    """A tiny world: whichever rows a test needs, and nothing else.

    Modeled on `backend/tests/integration/test_instrument_price.py`, extended
    with `benchmarked` for `benchmark_daily`.
    """

    def __init__(self, engine) -> None:
        self.engine = engine
        with Session(engine) as session:
            if session.exec(select(Account)).first() is None:
                session.add(
                    Account(id=uuid4(), broker="degiro", name="test", base_currency="EUR")
                )
                session.commit()

    def priced(self, on: date, adjusted: str, *, currency: str = "EUR") -> "Seed":
        with Session(self.engine) as session:
            session.add(
                PriceDaily(
                    id=uuid4(),
                    isin=ISIN,
                    price_date=on,
                    # Deliberately different, so a join that read the wrong
                    # column would produce a wrong number rather than the same
                    # one. This module must never see the doubled figure.
                    close_unadjusted=D(adjusted) * 2,
                    close_adjusted=D(adjusted),
                    currency=currency,
                    source="yahoo",
                    fetched_at=FETCHED,
                )
            )
            session.commit()
        return self

    def benchmarked(self, on: date, adjusted: str, *, currency: str = "EUR") -> "Seed":
        with Session(self.engine) as session:
            session.add(
                BenchmarkDaily(
                    id=uuid4(),
                    key=KEY,
                    price_date=on,
                    # Same rule as `priced`, for the same reason: `benchmark_daily`
                    # stores both closes and M3 reads exactly one of them.
                    close_unadjusted=D(adjusted) * 2,
                    close_adjusted=D(adjusted),
                    currency=currency,
                    source="yahoo",
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


@pytest.fixture(name="seeded")
def _seeded():
    """Two held runs with a flat stretch between them, instrument and benchmark
    both quoted in the base currency. The instrument beats the benchmark in
    each held run, and the flat stretch is where the benchmark runs away -- so
    an implementation that credited the owner with the gap would be visibly
    generous."""
    engine = create_engine_and_tables("sqlite://")
    seed = Seed(engine)
    for on, instrument, benchmark in (
        (HELD.start, "100.00", "50.00"),
        (HELD.end, "112.00", "54.00"),
        (MID_FLAT, "150.00", "90.00"),
        (HELD_AGAIN.start, "100.00", "50.00"),
        (HELD_AGAIN.end, "105.00", "51.00"),
    ):
        seed.priced(on, instrument).benchmarked(on, benchmark)
    return engine


@pytest.fixture(name="seeded_usd_benchmark")
def _seeded_usd_benchmark():
    """A dollar benchmark that rose 10% while the dollar fell 10% against the
    euro: 100 USD at 1.00 USD/EUR and 110 USD at 1.10 USD/EUR are both 100 EUR.
    A euro holder got nothing, and any pipeline that rebases before it converts
    reports 10%."""
    engine = create_engine_and_tables("sqlite://")
    (
        Seed(engine)
        .priced(HELD.start, "100.00")
        .priced(HELD.end, "112.00")
        .benchmarked(HELD.start, "100.00", currency="USD")
        .benchmarked(HELD.end, "110.00", currency="USD")
        .rate(HELD.start, "USD", "1.00")
        .rate(HELD.end, "USD", "1.10")
    )
    return engine


@pytest.fixture(name="seeded_no_benchmark")
def _seeded_no_benchmark():
    """The instrument is priced; `benchmark_daily` is empty. The comparison has
    to withhold a number rather than invent a zero."""
    engine = create_engine_and_tables("sqlite://")
    Seed(engine).priced(HELD.start, "100.00").priced(HELD.end, "112.00")
    return engine


def test_each_index_starts_at_one_hundred_at_its_interval_start(seeded) -> None:
    result = comparison(seeded, ISIN, benchmark_key=KEY, intervals=[HELD, FLAT, HELD_AGAIN])
    starts = [p.index for p in result.instrument_index if p.on in (HELD.start, HELD_AGAIN.start)]
    assert starts == [D("100"), D("100")]


def test_widening_the_window_does_not_move_any_excess_figure(seeded) -> None:
    """M3-6, stated as the property that makes it worth having."""
    narrow = comparison(seeded, ISIN, benchmark_key=KEY, intervals=[HELD])
    wide = comparison(seeded, ISIN, benchmark_key=KEY, intervals=[HELD, FLAT, HELD_AGAIN])
    assert narrow.intervals[0].excess == wide.intervals[0].excess


def test_out_of_market_intervals_get_no_excess_figure(seeded) -> None:
    """Parent doc Sec 7.6: over the actual holding period ONLY. Crediting the
    instrument with drift over a stretch the owner did not own it is the error
    this rules out."""
    result = comparison(seeded, ISIN, benchmark_key=KEY, intervals=[HELD, FLAT, HELD_AGAIN])
    flat = [i for i in result.intervals if i.start == FLAT.start]
    assert flat == []


def test_the_linked_return_chains_the_in_market_intervals_and_skips_the_gap(seeded) -> None:
    result = comparison(seeded, ISIN, benchmark_key=KEY, intervals=[HELD, FLAT, HELD_AGAIN])
    a, b = (i.instrument_return for i in result.intervals)
    assert a is not None and b is not None
    assert result.linked_instrument_return == (1 + a) * (1 + b) - 1


def test_a_foreign_benchmark_is_converted_before_it_is_rebased(seeded_usd_benchmark) -> None:
    """M3-7. The case that still produces a plausible number when you get it
    wrong: a dollar index up 10% while the dollar fell 10% did approximately
    nothing for a euro holder."""
    result = comparison(seeded_usd_benchmark, ISIN, benchmark_key=KEY, intervals=[HELD])
    assert result.intervals[0].benchmark_return == D("0")


def test_a_benchmark_with_no_data_yields_null_excess_and_a_reason(seeded_no_benchmark) -> None:
    result = comparison(seeded_no_benchmark, ISIN, benchmark_key=KEY, intervals=[HELD])
    assert result.intervals[0].excess is None
    assert result.intervals[0].reason
    assert result.linked_excess is None
    assert result.coverage == "missing"


def test_the_instrument_return_survives_a_benchmark_outage(seeded_no_benchmark) -> None:
    """The benchmark reports its own coverage. A provider that stopped serving
    an index must not take the owner's own return down with it -- the two are
    separate facts and the badge on this object is the benchmark's."""
    result = comparison(seeded_no_benchmark, ISIN, benchmark_key=KEY, intervals=[HELD])
    assert result.intervals[0].instrument_return == D("0.12")
    assert result.linked_instrument_return == D("0.12")


def test_the_basis_is_always_stated(seeded) -> None:
    """Parent doc Sec 7.4: no endpoint returns an unlabelled return."""
    result = comparison(seeded, ISIN, benchmark_key=KEY, intervals=[HELD])
    assert result.basis == "total_return"


def test_the_excess_is_the_difference_of_the_two_rebased_returns(seeded) -> None:
    """The instrument made 12% and the benchmark 8% over the first held run.
    Both are measured from the interval's own start, not the window's."""
    result = comparison(seeded, ISIN, benchmark_key=KEY, intervals=[HELD, FLAT, HELD_AGAIN])
    first = result.intervals[0]
    assert first.instrument_return == D("0.12")
    assert first.benchmark_return == D("0.08")
    assert first.excess == D("0.04")
    assert first.reason is None

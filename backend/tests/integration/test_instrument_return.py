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

#: Well inside HELD, and far enough past its start that the span rule calls a
#: series beginning here partial rather than merely late off a holiday.
LATE_IN_HELD = date(2026, 1, 26)
BENCH_LATE_IN_HELD = date(2026, 1, 20)
#: The second day of HELD: one day is inside `STALE_DAYS`, so a series that
#: loses only the first day is still judged to span the interval.
SECOND_DAY = date(2026, 1, 6)


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
    generous.

    **The second run's opening prices deliberately differ from the first
    run's.** They used to be identical, which made every rebasing assertion in
    this file pass against an index anchored at the window's first day instead
    of at each interval's own start -- the exact confusion M3-6 exists to
    remove, invisible to a fixture that cannot tell the two anchors apart.
    """
    engine = create_engine_and_tables("sqlite://")
    seed = Seed(engine)
    for on, instrument, benchmark in (
        (HELD.start, "100.00", "50.00"),
        (HELD.end, "112.00", "54.00"),
        (MID_FLAT, "150.00", "90.00"),
        (HELD_AGAIN.start, "200.00", "80.00"),
        (HELD_AGAIN.end, "210.00", "81.60"),
    ):
        seed.priced(on, instrument).benchmarked(on, benchmark)
    return engine


@pytest.fixture(name="seeded_late_instrument")
def _seeded_late_instrument():
    """The benchmark spans the whole holding; the instrument is priced only for
    its last few days. Both sides produce a number and the two numbers are not
    comparable."""
    engine = create_engine_and_tables("sqlite://")
    (
        Seed(engine)
        .priced(LATE_IN_HELD, "100.00")
        .priced(HELD.end, "101.00")
        .benchmarked(HELD.start, "50.00")
        .benchmarked(HELD.end, "70.00")
    )
    return engine


@pytest.fixture(name="seeded_late_benchmark")
def _seeded_late_benchmark():
    """The mirror image: the instrument spans the holding, the benchmark
    arrives two weeks into it."""
    engine = create_engine_and_tables("sqlite://")
    (
        Seed(engine)
        .priced(HELD.start, "100.00")
        .priced(HELD.end, "112.00")
        .benchmarked(BENCH_LATE_IN_HELD, "50.00")
        .benchmarked(HELD.end, "54.00")
    )
    return engine


@pytest.fixture(name="seeded_unconvertible_benchmark_day")
def _seeded_unconvertible_benchmark_day():
    """A dollar benchmark whose first day predates the first FX rate. That day
    cannot be expressed in the owner's currency at all, so it is dropped -- but
    the two days that bracket the return both convert, so the return still
    stands and only the badge should move."""
    engine = create_engine_and_tables("sqlite://")
    (
        Seed(engine)
        .priced(HELD.start, "100.00")
        .priced(HELD.end, "112.00")
        .benchmarked(HELD.start, "60.00", currency="USD")
        .benchmarked(SECOND_DAY, "50.00", currency="USD")
        .benchmarked(HELD.end, "55.00", currency="USD")
        .rate(SECOND_DAY, "USD", "1.00")
    )
    return engine


@pytest.fixture(name="seeded_second_run_unpriced")
def _seeded_second_run_unpriced():
    """Two held runs; the instrument has no prices at all in the second. The
    benchmark covers both."""
    engine = create_engine_and_tables("sqlite://")
    (
        Seed(engine)
        .priced(HELD.start, "100.00")
        .priced(HELD.end, "112.00")
        .benchmarked(HELD.start, "50.00")
        .benchmarked(HELD.end, "54.00")
        .benchmarked(HELD_AGAIN.start, "80.00")
        .benchmarked(HELD_AGAIN.end, "81.60")
    )
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
    """The second run opens at 200.00 where the window opens at 100.00, so an
    index anchored at the window edge would read 200 here instead of 100. With
    identical opening prices this assertion could not tell the two anchors
    apart, which is what it used to do."""
    result = comparison(seeded, ISIN, benchmark_key=KEY, intervals=[HELD, FLAT, HELD_AGAIN])
    starts = [p.index for p in result.instrument_index if p.on in (HELD.start, HELD_AGAIN.start)]
    assert starts == [D("100"), D("100")]
    ends = [p.index for p in result.instrument_index if p.on in (HELD.end, HELD_AGAIN.end)]
    assert ends == [D("112"), D("105")]


def test_widening_the_window_does_not_move_any_excess_figure(seeded) -> None:
    """M3-6, stated as the property that makes it worth having.

    The SECOND held run is the discriminating one. Comparing it alone against
    the full list moves the window's first day from 2026-03-02 back to
    2026-01-05 while leaving the interval's own start where it is; an index
    anchored at the window edge moves with it, one anchored at the interval
    start does not. The first run cannot show this -- it opens on the window's
    first day either way -- which is why the earlier version of this test went
    on passing against a window-edge anchor.
    """
    narrow = comparison(seeded, ISIN, benchmark_key=KEY, intervals=[HELD_AGAIN])
    wide = comparison(seeded, ISIN, benchmark_key=KEY, intervals=[HELD, FLAT, HELD_AGAIN])
    assert [i.start for i in wide.intervals] == [HELD.start, HELD_AGAIN.start]
    assert narrow.intervals[0].instrument_return == wide.intervals[1].instrument_return
    assert narrow.intervals[0].benchmark_return == wide.intervals[1].benchmark_return
    assert narrow.intervals[0].excess == wide.intervals[1].excess


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


def test_an_instrument_priced_for_part_of_the_holding_yields_no_excess(
    seeded_late_instrument,
) -> None:
    """The span rule applied to the instrument, which is where it was missing.

    The benchmark covers the whole holding and the instrument covers its last
    four days. Both sides produce a number, and 0.01 - 0.4 = -0.39 is
    arithmetic rather than a comparison: it says the instrument lagged by 39
    points over a stretch it was measured across for less than a week.

    Not contrived. `Interval.start` comes from `instrument_price._intervals`,
    whose points are `weekdays(start, end)` including days that could not be
    priced, so an interval routinely opens on a day with no price behind it.
    """
    result = comparison(seeded_late_instrument, ISIN, benchmark_key=KEY, intervals=[HELD])
    row = result.intervals[0]
    assert row.instrument_return == D("0.01")
    assert row.benchmark_return == D("0.4")
    assert row.excess is None
    assert ISIN in (row.reason or "")
    assert "not the whole of" in (row.reason or "")
    assert result.linked_excess is None
    # The badge on this object is the BENCHMARK's, and the benchmark is fine.
    assert result.coverage == "full"


def test_a_benchmark_covering_part_of_the_holding_yields_no_excess(
    seeded_late_benchmark,
) -> None:
    """The same rule on the other side, and the coverage value that goes with
    it. The benchmark's return is real over the days it did cover, so it is
    still reported; the excess is not, because differencing two returns
    measured over different spans is a subtraction that compiles and means
    nothing."""
    result = comparison(seeded_late_benchmark, ISIN, benchmark_key=KEY, intervals=[HELD])
    row = result.intervals[0]
    assert row.instrument_return == D("0.12")
    assert row.benchmark_return == D("0.08")
    assert row.excess is None
    assert KEY in (row.reason or "")
    assert "not the whole of" in (row.reason or "")
    assert result.coverage == "partial"
    assert result.linked_excess is None


def test_a_benchmark_day_that_cannot_be_converted_shows_on_the_badge(
    seeded_unconvertible_benchmark_day,
) -> None:
    """A day with no rate to reach the owner's currency is dropped rather than
    guessed at. It does not move an endpoint-to-endpoint return -- both
    endpoints here convert -- so the excess still stands, but the series the
    reader sees is shorter than the one the provider sent and the badge has to
    say so. Without that, a silently shortened series is indistinguishable from
    a complete one."""
    result = comparison(
        seeded_unconvertible_benchmark_day, ISIN, benchmark_key=KEY, intervals=[HELD]
    )
    row = result.intervals[0]
    assert row.benchmark_return == D("0.10")  # 50.00 -> 55.00 at 1.00 USD/EUR
    assert row.excess == D("0.02")
    assert row.reason is None
    assert result.coverage == "partial"
    assert [p.on for p in result.benchmark_index] == [SECOND_DAY, HELD.end]


def test_one_unmeasurable_run_makes_the_whole_linked_return_null(
    seeded_second_run_unpriced,
) -> None:
    """A chain missing a link is not a shorter chain.

    The instrument has no prices in the second held run. Skipping that run and
    linking the rest would report 12% -- the return of a holding period the
    owner never had. The benchmark keeps its own chain, because only one side
    lost a link.
    """
    result = comparison(
        seeded_second_run_unpriced, ISIN, benchmark_key=KEY, intervals=[HELD, HELD_AGAIN]
    )
    assert result.intervals[0].instrument_return == D("0.12")
    assert result.intervals[1].instrument_return is None
    assert result.linked_instrument_return is None
    assert result.linked_benchmark_return is not None
    assert result.linked_excess is None

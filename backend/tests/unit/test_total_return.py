"""The total-return series: the only reader of `close_adjusted`.

Not wired to an endpoint yet -- M3's instrument chart is what will consume it.
It exists now because parent doc Sec 7.5's guarantee is that no call path reaches
both closes, and a test asserting that valuation cannot reach a module that does
not exist would pass for the wrong reason.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlmodel import Session

from app.analytics.total_return import (
    benchmark_total_return_series,
    total_return_series,
)
from app.db import create_engine_and_tables
from app.models.market import BenchmarkDaily, PriceDaily

D = Decimal
FETCHED = datetime(2026, 9, 6, 12, 0, 0)

@pytest.fixture(name="engine")
def _engine():
    engine = create_engine_and_tables("sqlite://")
    with Session(engine) as session:
        for day, plain, adjusted in (
            (date(2025, 3, 3), "20.00", "10.00"),
            (date(2025, 3, 4), "20.00", "11.00"),
            (date(2025, 3, 5), "20.00", "12.00"),
        ):
            session.add(
                PriceDaily(
                    id=uuid4(),
                    isin="NL0000000001",
                    price_date=day,
                    close_unadjusted=D(plain),
                    close_adjusted=D(adjusted),
                    currency="EUR",
                    source="yahoo",
                    fetched_at=FETCHED,
                )
            )
        session.commit()
    return engine

def test_reads_the_adjusted_close(engine) -> None:
    """The fixture holds a flat plain close and a rising adjusted one, so a
    series reading the wrong column would be flat -- and a flat total return on
    a dividend payer is exactly the bug Sec 7.5 is about, seen from the other
    side."""
    points = total_return_series(
        engine, "NL0000000001", start=date(2025, 3, 3), end=date(2025, 3, 5)
    )
    assert [p.close_adjusted for p in points] == [D("10.00"), D("11.00"), D("12.00")]

def test_carries_the_quoted_currency_so_the_caller_can_convert(engine) -> None:
    """Replaces `test_rebases_to_one_at_the_start_of_the_window`. The requirement
    changed rather than the test being wrong: M3-6 rebases at each in-market
    interval's start, not at the window edge, so rebasing here would produce a
    figure that moves when the reader changes the range control. M3-7 needs the
    currency instead, because an index built before converting is the return a
    local investor got, not the one the owner got."""
    points = total_return_series(
        engine, "NL0000000001", start=date(2025, 3, 3), end=date(2025, 3, 5)
    )
    assert {p.currency for p in points} == {"EUR"}

def test_the_window_is_inclusive_and_excludes_nothing_else(engine) -> None:
    points = total_return_series(
        engine, "NL0000000001", start=date(2025, 3, 4), end=date(2025, 3, 4)
    )
    assert [p.on for p in points] == [date(2025, 3, 4)]

def test_an_instrument_with_no_prices_yields_an_empty_series(engine) -> None:
    assert total_return_series(
        engine, "NL0000000009", start=date(2025, 3, 3), end=date(2025, 3, 5)
    ) == ()


@pytest.fixture(name="benchmark_engine")
def _benchmark_engine():
    """A benchmark proxy quoted in USD, with the two closes deliberately
    different so a reader that took the wrong column would produce a wrong
    number rather than the same one."""
    engine = create_engine_and_tables("sqlite://")
    with Session(engine) as session:
        for day, plain, adjusted in (
            (date(2025, 3, 3), "40.00", "20.00"),
            (date(2025, 3, 4), "40.00", "22.00"),
            (date(2025, 3, 5), "40.00", "24.00"),
        ):
            session.add(
                BenchmarkDaily(
                    id=uuid4(),
                    key="world",
                    price_date=day,
                    close_unadjusted=D(plain),
                    close_adjusted=D(adjusted),
                    currency="USD",
                    source="yahoo",
                    fetched_at=FETCHED,
                )
            )
        session.commit()
    return engine

def test_the_benchmark_reader_reads_the_adjusted_close(benchmark_engine) -> None:
    """Same shape as the instrument reader, same column. The fixture's plain
    close is flat and its adjusted close rises, so a reader taking the wrong
    column would report a benchmark that went nowhere."""
    points = benchmark_total_return_series(
        benchmark_engine, "world", start=date(2025, 3, 3), end=date(2025, 3, 5)
    )
    assert [p.close_adjusted for p in points] == [D("20.00"), D("22.00"), D("24.00")]

def test_the_benchmark_carries_its_own_quoted_currency(benchmark_engine) -> None:
    """A benchmark quoted abroad is the case M3-7 exists for: the caller has to
    convert before it rebases, and it cannot do that without this field."""
    points = benchmark_total_return_series(
        benchmark_engine, "world", start=date(2025, 3, 3), end=date(2025, 3, 5)
    )
    assert {p.currency for p in points} == {"USD"}

def test_a_benchmark_with_no_rows_yields_an_empty_series(benchmark_engine) -> None:
    assert benchmark_total_return_series(
        benchmark_engine, "nowhere", start=date(2025, 3, 3), end=date(2025, 3, 5)
    ) == ()

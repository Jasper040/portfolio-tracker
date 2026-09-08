"""The benchmark proxy's adjusted closes: the harmless half of the adjusted lane.

Split out of `test_total_return.py` in M6a, alongside the module it covers. The
cut is about danger rather than tidiness -- a benchmark is never held and appears
in no cash balance, so reaching this reader beside a valuation path counts nothing
twice, whereas reaching an instrument's adjusted closes does. That asymmetry is
asserted structurally in `tests/integration/test_no_double_count.py`; what is here
is the behaviour.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlmodel import Session

from app.analytics.benchmark_return import benchmark_total_return_series
from app.db import create_engine_and_tables
from app.models.market import BenchmarkDaily

D = Decimal
FETCHED = datetime(2026, 9, 6, 12, 0, 0)


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

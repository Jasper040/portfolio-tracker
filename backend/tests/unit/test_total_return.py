"""One instrument's adjusted closes: the dangerous half of the adjusted lane.

M3's instrument chart consumes this. It is the reader that must stay away from any
path holding a cash balance, because `close_adjusted` already contains every
dividend and cash contains them again -- parent doc Sec 7.5.

The benchmark reader moved to `test_benchmark_return.py` in M6a, with the module
it covers. See that file, and `analytics/benchmark_return.py`, for why the two are
not equally dangerous and therefore should not have shared a module.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlmodel import Session

from app.analytics.total_return import total_return_series
from app.db import create_engine_and_tables
from app.models.market import PriceDaily

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

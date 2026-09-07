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

def test_rebases_to_one_at_the_start_of_the_window(engine) -> None:
    points = total_return_series(
        engine, "NL0000000001", start=date(2025, 3, 3), end=date(2025, 3, 5)
    )
    assert points[0].index == D("1")
    assert points[-1].index == D("1.2")

def test_the_window_is_inclusive_and_excludes_nothing_else(engine) -> None:
    points = total_return_series(
        engine, "NL0000000001", start=date(2025, 3, 4), end=date(2025, 3, 4)
    )
    assert [p.on for p in points] == [date(2025, 3, 4)]

def test_an_instrument_with_no_prices_yields_an_empty_series(engine) -> None:
    assert total_return_series(
        engine, "NL0000000009", start=date(2025, 3, 3), end=date(2025, 3, 5)
    ) == ()

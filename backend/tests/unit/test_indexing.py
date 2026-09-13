"""The shared index arithmetic, asserted on its own now that two comparisons use it.

Split out of `instrument_return.py` in M6a. Everything here was already exercised
through `comparison()` in `tests/integration/test_instrument_return.py`. What those
tests could not say is that the helpers work WITHOUT an instrument -- which is the
reason they moved, because the portfolio comparison has none.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal as D
from uuid import uuid4

from app.analytics.adjusted import TotalReturnPoint
from app.analytics.indexing import (
    SPAN_FULL,
    SPAN_MISSING,
    SPAN_PARTIAL,
    BasePoint,
    in_base_points,
    link,
    rebase,
    side_reason,
    span_coverage,
    worst_span,
)
from app.models.market import FxDaily

MON = date(2025, 3, 3)
TUE = date(2025, 3, 4)
WED = date(2025, 3, 5)
FRI = date(2025, 3, 7)
NEXT_MON = date(2025, 3, 10)
NEXT_FRI = date(2025, 3, 14)


def test_rebase_indexes_to_100_at_the_start_and_returns_the_ratio() -> None:
    points = (BasePoint(MON, D("50")), BasePoint(TUE, D("55")), BasePoint(WED, D("60")))
    index, measured = rebase(points, MON, WED)
    assert [p.index for p in index] == [D("100"), D("110"), D("120")]
    assert measured == D("0.2")


def test_rebase_ignores_points_outside_the_span() -> None:
    points = (BasePoint(MON, D("40")), BasePoint(TUE, D("50")), BasePoint(WED, D("55")))
    index, measured = rebase(points, TUE, WED)
    assert [p.on for p in index] == [TUE, WED]
    assert measured == D("0.1")


def test_an_empty_or_zero_based_span_measures_nothing() -> None:
    assert rebase((), MON, WED) == ((), None)
    assert rebase((BasePoint(MON, D("0")),), MON, WED) == ((), None)


def test_a_series_starting_well_after_the_span_covers_only_part_of_it() -> None:
    points = (BasePoint(NEXT_MON, D("50")), BasePoint(NEXT_FRI, D("55")))
    _, measured = rebase(points, MON, NEXT_FRI)
    assert span_coverage(points, MON, NEXT_FRI, measured) == SPAN_PARTIAL


def test_a_weekend_at_the_edge_of_the_span_is_not_a_shortfall() -> None:
    points = (BasePoint(TUE, D("50")), BasePoint(FRI, D("55")))
    _, measured = rebase(points, MON, FRI)
    assert span_coverage(points, MON, FRI, measured) == SPAN_FULL


def test_nothing_measured_is_missing() -> None:
    assert span_coverage((), MON, FRI, None) == SPAN_MISSING


def test_worst_span_ranks_missing_above_partial_above_full() -> None:
    assert worst_span([SPAN_FULL, SPAN_PARTIAL]) == SPAN_PARTIAL
    assert worst_span([SPAN_PARTIAL, SPAN_MISSING, SPAN_FULL]) == SPAN_MISSING
    assert worst_span([]) == SPAN_FULL


def test_link_chains_and_refuses_a_missing_link() -> None:
    assert link([D("0.1"), D("0.1")]) == D("0.21")
    assert link([D("0.1"), None]) is None
    assert link([]) == D("0")


def test_conversion_divides_by_the_rate_and_counts_what_it_drops() -> None:
    rates = {
        ("USD", "EUR"): [
            FxDaily(
                id=uuid4(), from_ccy="USD", to_ccy="EUR", rate_date=MON, rate=D("1.25"),
                source="ecb", fetched_at=datetime(2026, 9, 6, 12, 0, 0),
            )
        ]
    }
    points = (
        TotalReturnPoint(on=date(2025, 2, 28), close_adjusted=D("50"), currency="USD"),
        TotalReturnPoint(on=MON, close_adjusted=D("50"), currency="USD"),
    )
    converted, dropped = in_base_points(points, "EUR", rates)
    assert converted == (BasePoint(on=MON, value=D("40")),)
    assert dropped == 1


def test_side_reason_names_the_span_it_fell_short_of() -> None:
    points = (BasePoint(NEXT_MON, D("50")), BasePoint(NEXT_FRI, D("55")))
    assert side_reason("benchmark 'world'", points, MON, NEXT_FRI, SPAN_PARTIAL) == (
        "benchmark 'world' covers 2025-03-10 to 2025-03-14, "
        "not the whole of 2025-03-03 to 2025-03-14"
    )
    assert side_reason("x", (), MON, FRI, SPAN_MISSING) == "no x data from 2025-03-03 to 2025-03-07"
    assert side_reason("x", points, MON, FRI, SPAN_FULL) is None

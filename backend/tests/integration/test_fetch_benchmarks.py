"""The benchmark phase (M3 section 4.4).

Two behaviours differ from the instrument phase and both are here: a symbol
that returns nothing is a hard error naming the slug rather than a quarantine
entry, and a benchmark failure does not block the instrument phase.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest
from sqlmodel import Session, select

from app.db import create_engine_and_tables
from app.ingest.benchmarks import Benchmark
from app.ingest.prices import BenchmarkFetchReport, fetch_benchmarks
from app.models.market import BenchmarkDaily
from app.providers.base import PricePoint, PriceSeries

TODAY = date(2026, 9, 7)

WORLD = Benchmark(key="world", symbol="AAA.XX", currency="EUR",
                  name="A proxy", ter=D("0.20"))


@pytest.fixture(name="engine")
def _engine():
    return create_engine_and_tables("sqlite://")


class FakeProvider:
    """Returns a fixed two-day series for one symbol and nothing for anything
    else. A `None` return is how the real provider reports "no such symbol"."""

    def __init__(self, known: dict[str, PriceSeries] | None = None) -> None:
        self.known = known or {}
        self.calls: list[tuple[str, date | None]] = []

    def full_series(self, symbol: str) -> PriceSeries | None:
        self.calls.append((symbol, None))
        return self.known.get(symbol)

    def series_since(self, symbol: str, since: date) -> PriceSeries | None:
        self.calls.append((symbol, since))
        return self.known.get(symbol)


def series(*days: tuple[date, str, str]) -> PriceSeries:
    return PriceSeries(
        symbol="AAA.XX",
        currency="EUR",
        source="fake",
        points=tuple(
            PricePoint(on=on, close_unadjusted=D(u), close_adjusted=D(a))
            for on, u, a in days
        ),
    )


TWO_DAYS = (
    (date(2026, 9, 3), "100.00", "98.00"),
    (date(2026, 9, 4), "101.00", "99.00"),
)


def test_writes_a_row_per_day(engine) -> None:
    provider = FakeProvider({"AAA.XX": series(*TWO_DAYS)})
    report = fetch_benchmarks(engine, provider, [WORLD], today=TODAY)

    assert report.fetched == ("world",)
    assert report.failed == ()
    with Session(engine) as session:
        rows = sorted(session.exec(select(BenchmarkDaily)).all(),
                      key=lambda r: r.price_date)
    assert [r.price_date for r in rows] == [date(2026, 9, 3), date(2026, 9, 4)]
    assert [r.key for r in rows] == ["world", "world"]
    assert rows[0].close_adjusted == D("98.00")


def test_an_unknown_symbol_is_a_named_failure_not_an_exception(engine) -> None:
    """M3 section 4.4: the config is the answer, so the only useful report is
    that the answer is wrong. Naming the slug is the whole value."""
    report = fetch_benchmarks(engine, FakeProvider(), [WORLD], today=TODAY)

    assert report.fetched == ()
    assert len(report.failed) == 1
    key, reason = report.failed[0]
    assert key == "world"
    assert "AAA.XX" in reason


def test_a_second_run_writes_nothing_new(engine) -> None:
    """Idempotent, like the instrument phase. The unique constraint would raise
    on a re-insert, so this failing looks like a crash rather than a duplicate."""
    provider = FakeProvider({"AAA.XX": series(*TWO_DAYS)})
    fetch_benchmarks(engine, provider, [WORLD], today=TODAY)
    second = fetch_benchmarks(engine, provider, [WORLD], today=TODAY)

    assert second.rows_written == 0
    with Session(engine) as session:
        assert len(session.exec(select(BenchmarkDaily)).all()) == 2


def test_the_second_run_is_incremental(engine) -> None:
    provider = FakeProvider({"AAA.XX": series(*TWO_DAYS)})
    fetch_benchmarks(engine, provider, [WORLD], today=TODAY)
    provider.calls.clear()
    fetch_benchmarks(engine, provider, [WORLD], today=TODAY)

    assert provider.calls == [("AAA.XX", date(2026, 9, 4))]


def test_full_refetches_the_whole_window(engine) -> None:
    provider = FakeProvider({"AAA.XX": series(*TWO_DAYS)})
    fetch_benchmarks(engine, provider, [WORLD], today=TODAY)
    provider.calls.clear()
    fetch_benchmarks(engine, provider, [WORLD], full=True, today=TODAY)

    assert provider.calls == [("AAA.XX", None)]


def test_one_failing_benchmark_does_not_stop_the_next(engine) -> None:
    other = Benchmark(key="europe", symbol="BBB.XX", currency="EUR",
                      name="Another", ter=D("0.12"))
    provider = FakeProvider({"BBB.XX": series(*TWO_DAYS)})
    report = fetch_benchmarks(engine, provider, [WORLD, other], today=TODAY)

    assert report.fetched == ("europe",)
    assert [key for key, _ in report.failed] == ["world"]


def test_no_benchmarks_configured_is_not_an_error(engine) -> None:
    report = fetch_benchmarks(engine, FakeProvider(), [], today=TODAY)
    assert report == BenchmarkFetchReport(fetched=(), failed=(), rows_written=0)

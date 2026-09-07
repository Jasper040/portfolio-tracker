"""The instrument chart's acceptance (M3 sections 5.2-5.4), against the owner's
own export. Task 10 of M3 -- the milestone's final proof.

Modeled on `test_realdata_prices.py`: reads the operator's own populated
database rather than fetching, so the opt-in suite stays as network-free as
CI, and skips with the same reason that suite uses when the price cache is
empty -- the ordinary state before `fetch-prices` has been run.

States no figure of its own. The instrument this file exercises is picked at
run time by `realdata_subject.instrument_with_most_in_market_intervals`,
which reads `Transactions.csv` directly rather than `position_daily` -- see
that function's own docstring for why an oracle sharing its parser with the
code under test would be worthless.

`config/benchmarks.yaml` ships with no active entries, so the benchmark-side
assertions skip with their own explicit reason rather than failing on an
empty tuple or passing vacuously over zero rows.

To run it for real:

    cd backend
    python -m app.cli import ../degiro-export
    python -m app.cli fetch-prices
    python -m app.cli rebuild
    python -m pytest -q -m realdata
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.analytics.instrument_price import InstrumentPriceView, instrument_price_view
from app.analytics.instrument_return import comparison
from app.db import create_engine_and_tables
from app.ingest.benchmarks import load_benchmarks
from app.models.ledger import PositionDaily
from app.models.market import PriceDaily
from tests.integration import realdata_subject as subject

EXPORT = Path(__file__).parents[3] / "degiro-export"
BENCHMARKS_CONFIG = subject.REPO / "config" / "benchmarks.yaml"
URL = subject.local_database_url()

D = Decimal

pytestmark = [
    pytest.mark.realdata,
    pytest.mark.skipif(
        not (EXPORT / "Transactions.csv").exists() or URL is None,
        reason="no real export, or no local database in backend/.env",
    ),
]


@pytest.fixture(scope="module")
def engine() -> Engine:
    assert URL is not None
    built = create_engine_and_tables(URL)
    with Session(built) as session:
        if not session.exec(select(PriceDaily)).first():
            pytest.skip("price cache is empty; run `python -m app.cli fetch-prices` first")
        if not session.exec(select(PositionDaily)).first():
            pytest.skip("no daily series; run `python -m app.cli rebuild` first")
    return built


@pytest.fixture(scope="module")
def derived() -> tuple[str, int]:
    """The oracle's pick, computed once so every test that needs it agrees on
    the same instrument. Independent of `engine` -- see the function's own
    docstring -- so this fixture (and the test below that uses only it) runs
    even while the cache-gated tests skip."""
    return subject.instrument_with_most_in_market_intervals()


def _view_for(engine: Engine, isin: str) -> InstrumentPriceView:
    """The chart for `isin`, anchored on the newest day the cache actually
    priced it -- never `date.today()`. `routes_instrument.py` makes the same
    choice for the same reason: a fetch lag would otherwise show up as a hole
    in `held` coverage that has nothing to do with the pipeline under test."""
    with Session(engine) as session:
        latest = session.exec(
            select(PriceDaily)
            .where(PriceDaily.isin == isin)
            .order_by(PriceDaily.price_date.desc())  # type: ignore[attr-defined]
            .limit(1)
        ).first()
    if latest is None:
        pytest.skip("the oracle's instrument has no price coverage in the cache")
    return instrument_price_view(
        engine, isin, start=subject.first_trade_date(), end=latest.price_date
    )


def test_the_derivation_finds_an_instrument_with_at_least_three_in_market_runs(
    derived: tuple[str, int],
) -> None:
    """The floor the brief states: at least three in-market intervals, found
    without ever touching `position_daily`."""
    isin, count = derived
    assert isin in subject.traded_isins()
    assert count >= 3


class TestTheInMarketIntervalsAgreeWithTheOracle:
    def test_alternate_strictly_and_meet_the_oracles_floor(
        self, engine: Engine, derived: tuple[str, int]
    ) -> None:
        """The pipeline's own `Interval` tuple, checked against a count the
        pipeline had no hand in producing."""
        isin, minimum = derived
        view = _view_for(engine, isin)
        flags = [interval.in_market for interval in view.intervals]
        assert all(a != b for a, b in zip(flags, flags[1:], strict=False)), (
            "two consecutive intervals share an in_market flag"
        )
        assert sum(flags) >= minimum


class TestEveryHeldDayIsPriced:
    def test_no_gap_on_a_day_the_position_was_open(
        self, engine: Engine, derived: tuple[str, int]
    ) -> None:
        isin, _ = derived
        view = _view_for(engine, isin)
        unpriceable = [point.on for point in view.points if point.held and point.close_base is None]
        assert not unpriceable, (
            f"{len(unpriceable)} held day(s) with no price, first {unpriceable[0]}"
        )


class TestTheBenchmarkComparison:
    """Sections 5.3/5.4. Every assertion here needs a configured benchmark,
    which the repo currently has none of -- so each skips explicitly rather
    than failing on a `comparison()` call with no key to pass."""

    def _benchmark_key(self) -> str:
        benchmarks = load_benchmarks(BENCHMARKS_CONFIG)
        if not benchmarks:
            pytest.skip("no benchmark configured in config/benchmarks.yaml")
        return benchmarks[0].key

    def test_every_in_market_interval_gets_a_reported_row(
        self, engine: Engine, derived: tuple[str, int]
    ) -> None:
        isin, _ = derived
        key = self._benchmark_key()
        view = _view_for(engine, isin)
        result = comparison(engine, isin, benchmark_key=key, intervals=view.intervals)
        held_starts = [interval.start for interval in view.intervals if interval.in_market]
        assert [row.start for row in result.intervals] == held_starts

    def test_every_excess_is_a_number_or_null_with_a_reason(
        self, engine: Engine, derived: tuple[str, int]
    ) -> None:
        isin, _ = derived
        key = self._benchmark_key()
        view = _view_for(engine, isin)
        result = comparison(engine, isin, benchmark_key=key, intervals=view.intervals)
        assert result.intervals, "no in-market interval to check the excess of"
        for row in result.intervals:
            if row.excess is None:
                assert row.reason, f"null excess with no reason for {row.start}..{row.end}"
            else:
                assert isinstance(row.excess, Decimal)
                assert row.reason is None

    def test_no_intervals_rebased_index_starts_anywhere_but_100(
        self, engine: Engine, derived: tuple[str, int]
    ) -> None:
        isin, _ = derived
        key = self._benchmark_key()
        view = _view_for(engine, isin)
        result = comparison(engine, isin, benchmark_key=key, intervals=view.intervals)
        held = [interval for interval in view.intervals if interval.in_market]
        assert held, "the oracle's instrument has no in-market interval to check"

        checked = 0
        for interval in held:
            for series in (result.instrument_index, result.benchmark_index):
                window = [point for point in series if interval.start <= point.on <= interval.end]
                if not window:
                    continue
                assert window[0].index == D("100"), (interval.start, window[0].on)
                checked += 1
        assert checked, "no rebased index series reached any in-market interval"

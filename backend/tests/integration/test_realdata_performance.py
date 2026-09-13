"""M6a's acceptance, against the owner's own export and price cache.

States no figure of its own. The flow oracle reads `Account.csv` directly; every
other assertion is a property the return series must have whatever the figures
are. The flow checks need only the export; the series checks read the
operator's populated database, and skip with the suite's usual reason when the
price cache is empty.

To run it for real:

    cd backend
    python -m app.cli import ../degiro-export
    python -m app.cli fetch-prices
    python -m app.cli rebuild
    python -m pytest -q -m realdata
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, col, select

from app.analytics.flows import EXTERNAL_FLOW_TYPES, external_flows
from app.analytics.portfolio_benchmark import compare_to_benchmark
from app.analytics.portfolio_return import PortfolioReturn, portfolio_returns
from app.analytics.valuation import value_series
from app.db import create_engine_and_tables
from app.ingest.benchmarks import load_benchmarks
from app.ingest.importer import ensure_default_account, import_degiro_export
from app.models.ledger import PositionDaily, Transaction
from app.models.market import BenchmarkDaily, PriceDaily
from tests.integration import realdata_subject as subject

D = Decimal
URL = subject.local_database_url()
BENCHMARKS_CONFIG = subject.REPO / "config" / "benchmarks.yaml"

pytestmark = [
    pytest.mark.realdata,
    pytest.mark.skipif(
        not subject.available(), reason="real DeGiro export or answers file not present"
    ),
]


@pytest.fixture(scope="module")
def imported() -> Engine:
    """The export alone, in memory. No prices are needed to count flows."""
    engine = create_engine_and_tables("sqlite://")
    import_degiro_export(engine, subject.EXPORT, ensure_default_account(engine), subject.ANSWERS)
    return engine


@pytest.fixture(scope="module")
def local() -> Engine:
    if URL is None:
        pytest.skip("no local database in backend/.env")
    engine = create_engine_and_tables(URL)
    with Session(engine) as session:
        if not session.exec(select(PriceDaily)).first():
            pytest.skip("price cache is empty; run `python -m app.cli fetch-prices` first")
        if not session.exec(select(PositionDaily)).first():
            pytest.skip("no daily series; run `python -m app.cli rebuild` first")
    return engine


@pytest.fixture(scope="module")
def measured(local: Engine) -> PortfolioReturn:
    return portfolio_returns(value_series(local, start=date.min).points, external_flows(local))


class TestFlows:
    def test_the_ledger_holds_exactly_the_flows_the_cash_book_states(
        self, imported: Engine
    ) -> None:
        """M6a-6 against the real export: every boundary-crossing row, and not
        one of the internal transfers that look exactly like them."""
        count, total = subject.external_flow_total()
        assert count > 0, "the export states no external flow; the check would be vacuous"
        with Session(imported) as session:
            rows = session.exec(
                select(Transaction).where(
                    col(Transaction.txn_type).in_(sorted(EXTERNAL_FLOW_TYPES))
                )
            ).all()
        assert len(rows) == count
        assert sum(external_flows(imported).values(), D("0")) == total


class TestTheSeries:
    def test_measures_something(self, measured: PortfolioReturn) -> None:
        assert measured.links, "no link to measure; the checks below would be vacuous"

    def test_every_run_is_the_product_of_its_links(self, measured: PortfolioReturn) -> None:
        for run in measured.runs:
            growth = D("1")
            for step in run.links:
                assert step.daily_return is not None
                growth *= D("1") + step.daily_return
            assert growth - 1 == run.linked_return

    def test_a_window_figure_exists_exactly_when_there_is_no_gap(
        self, measured: PortfolioReturn
    ) -> None:
        assert (measured.linked_return is None) == (measured.gaps > 0)

    def test_a_large_flow_does_not_read_as_a_return(
        self, local: Engine, measured: PortfolioReturn
    ) -> None:
        """A flow at least half the portfolio it lands in. Left in the numerator
        it would read as a return of roughly its own relative size; handled
        correctly, the day's return is the market's. The days are picked from
        the data, and the check skips if the ledger has none."""
        closes = {
            point.on: point.value_base for point in value_series(local, start=date.min).points
        }
        large: list[tuple[date, Decimal, Decimal]] = []
        for step in measured.links:
            before = closes.get(step.since)
            if step.daily_return is None or before is None or before <= 0 or step.flow_base == 0:
                continue
            relative = abs(step.flow_base) / before
            if relative >= D("0.5"):
                large.append((step.on, step.daily_return, relative))
        if not large:
            pytest.skip("no flow in the ledger is at least half the portfolio it landed in")
        for on, daily, relative in large:
            assert abs(daily) < relative / 2, on


class TestTheBenchmark:
    def test_every_run_is_compared_over_its_own_span(
        self, local: Engine, measured: PortfolioReturn
    ) -> None:
        benchmarks = load_benchmarks(BENCHMARKS_CONFIG)
        if not benchmarks:
            pytest.skip("config/benchmarks.yaml configures no benchmark")
        with Session(local) as session:
            if not session.exec(select(BenchmarkDaily)).first():
                pytest.skip("benchmark cache is empty; run `python -m app.cli fetch-prices` first")
        comparison = compare_to_benchmark(
            local, measured.runs,
            window_return=measured.linked_return, benchmark_key=benchmarks[0].key,
        )
        assert [(row.start, row.end) for row in comparison.runs] == [
            (run.start, run.end) for run in measured.runs
        ]
        for row in comparison.runs:
            assert (row.excess is None) == (row.reason is not None)

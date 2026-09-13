"""The portfolio against a benchmark: per run, labelled, never across a gap.

The runs are real `portfolio_returns` output over hand-built closes, so the
structural `MeasuredRun` contract is exercised by the type it exists for. The
benchmark is seeded into `benchmark_daily` because the conversion and span rules
read it from there.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

from app.analytics.portfolio_benchmark import compare_to_benchmark
from app.analytics.portfolio_return import PortfolioReturn, portfolio_returns
from app.analytics.valuation import ValuationPoint
from tests.integration.synthetic_ledger import Ledger

MON = date(2025, 3, 3)
TUE = date(2025, 3, 4)
WED = date(2025, 3, 5)
THU = date(2025, 3, 6)
FRI = date(2025, 3, 7)
NEXT_THU = date(2025, 3, 13)
NEXT_FRI = date(2025, 3, 14)


def day(on: date, value: str | None) -> ValuationPoint:
    return ValuationPoint(
        on=on,
        holdings_base=None if value is None else D("0.00"),
        cash_base=D("0.00") if value is None else D(value),
        value_base=None if value is None else D(value),
        coverage="missing" if value is None else "full",
        covered_pct=None if value is None else D("1"),
    )


def _one_run() -> PortfolioReturn:
    """1000.00 to 1100.00, Monday to Tuesday: 0.1."""
    return portfolio_returns([day(MON, "1000.00"), day(TUE, "1100.00")], {})


def _gapped() -> PortfolioReturn:
    """0.1 from Monday to Tuesday, no valuation Wednesday, 0.05 Thursday to Friday."""
    return portfolio_returns(
        [
            day(MON, "1000.00"), day(TUE, "1100.00"), day(WED, None),
            day(THU, "1200.00"), day(FRI, "1260.00"),
        ],
        {},
    )


class TestOneRun:
    def test_the_excess_is_the_portfolio_return_less_the_benchmarks(self) -> None:
        ledger = Ledger().benchmark(MON, "50.00").benchmark(TUE, "52.00")
        measured = _one_run()
        comparison = compare_to_benchmark(
            ledger.engine, measured.runs,
            window_return=measured.linked_return, benchmark_key="world",
        )
        (row,) = comparison.runs
        assert row.portfolio_return == D("0.1")
        assert row.benchmark_return == D("0.04")
        assert row.excess == D("0.06")
        assert (comparison.benchmark_return, comparison.excess) == (D("0.04"), D("0.06"))
        assert comparison.span == "full"

    def test_a_foreign_benchmark_is_converted_before_it_is_rebased(self) -> None:
        """M3-7. Flat at 50.00 in dollars while the rate moves from 1.00 to 1.25:
        in euros it fell from 50.00 to 40.00, which is what the owner would have
        got."""
        ledger = (
            Ledger()
            .benchmark(MON, "50.00", currency="USD")
            .benchmark(TUE, "50.00", currency="USD")
            .rate(MON, "USD", "1.00")
            .rate(TUE, "USD", "1.25")
        )
        measured = _one_run()
        comparison = compare_to_benchmark(
            ledger.engine, measured.runs,
            window_return=measured.linked_return, benchmark_key="world",
        )
        assert comparison.runs[0].benchmark_return == D("-0.2")

    def test_both_sides_are_labelled(self) -> None:
        """M6a-10: the benchmark's figure is a total return with dividends
        reinvested gross; the portfolio's labels ride on `PortfolioReturn`."""
        measured = _one_run()
        comparison = compare_to_benchmark(
            Ledger().engine, measured.runs,
            window_return=measured.linked_return, benchmark_key="world",
        )
        assert (comparison.basis, comparison.dividends) == ("total_return", "reinvested_gross")

    def test_the_benchmark_index_is_100_at_the_run_start(self) -> None:
        ledger = Ledger().benchmark(MON, "50.00").benchmark(TUE, "52.00")
        measured = _one_run()
        comparison = compare_to_benchmark(
            ledger.engine, measured.runs,
            window_return=measured.linked_return, benchmark_key="world",
        )
        assert [(p.on, p.index) for p in comparison.benchmark_index] == [
            (MON, D("100")), (TUE, D("104")),
        ]


class TestSpan:
    def test_no_benchmark_data_is_missing_with_a_reason_and_no_excess(self) -> None:
        measured = _one_run()
        comparison = compare_to_benchmark(
            Ledger().engine, measured.runs,
            window_return=measured.linked_return, benchmark_key="world",
        )
        (row,) = comparison.runs
        assert row.benchmark_return is None
        assert row.excess is None
        assert row.span == "missing"
        assert "world" in (row.reason or "")
        assert comparison.excess is None
        assert comparison.span == "missing"

    def test_a_benchmark_starting_late_yields_a_return_but_no_excess(self) -> None:
        """Differencing two returns measured over different spans is a
        subtraction that compiles and means nothing."""
        closes = [
            day(on, "1000.00")
            for on in (MON, TUE, WED, THU, FRI, date(2025, 3, 10), date(2025, 3, 11),
                       date(2025, 3, 12), NEXT_THU, NEXT_FRI)
        ]
        measured = portfolio_returns(closes, {})
        ledger = Ledger().benchmark(NEXT_THU, "50.00").benchmark(NEXT_FRI, "51.00")
        comparison = compare_to_benchmark(
            ledger.engine, measured.runs,
            window_return=measured.linked_return, benchmark_key="world",
        )
        (row,) = comparison.runs
        assert row.benchmark_return is not None
        assert row.excess is None
        assert row.span == "partial"
        assert "not the whole of" in (row.reason or "")

    def test_a_day_that_cannot_be_converted_marks_the_span_partial(self) -> None:
        """The drawn series is shorter than the one the provider sent."""
        ledger = (
            Ledger()
            .benchmark(MON, "50.00", currency="USD")
            .benchmark(TUE, "50.00", currency="USD")
            .rate(TUE, "USD", "1.25")
        )
        measured = _one_run()
        comparison = compare_to_benchmark(
            ledger.engine, measured.runs,
            window_return=measured.linked_return, benchmark_key="world",
        )
        assert comparison.span == "partial"


class TestGaps:
    def _ledger(self) -> Ledger:
        return (
            Ledger()
            .benchmark(MON, "50.00").benchmark(TUE, "51.00").benchmark(WED, "52.00")
            .benchmark(THU, "50.00").benchmark(FRI, "51.00")
        )

    def test_each_run_is_compared_over_its_own_span(self) -> None:
        measured = _gapped()
        comparison = compare_to_benchmark(
            self._ledger().engine, measured.runs,
            window_return=measured.linked_return, benchmark_key="world",
        )
        assert [(row.start, row.end, row.excess) for row in comparison.runs] == [
            (MON, TUE, D("0.08")),
            (THU, FRI, D("0.03")),
        ]

    def test_no_window_figure_spans_the_gap(self) -> None:
        """M6a-7, with a benchmark subtracted: still forbidden."""
        measured = _gapped()
        comparison = compare_to_benchmark(
            self._ledger().engine, measured.runs,
            window_return=measured.linked_return, benchmark_key="world",
        )
        assert comparison.benchmark_return is None
        assert comparison.excess is None

    def test_the_benchmark_index_restarts_at_100_on_each_run(self) -> None:
        measured = _gapped()
        comparison = compare_to_benchmark(
            self._ledger().engine, measured.runs,
            window_return=measured.linked_return, benchmark_key="world",
        )
        starts = [p.index for p in comparison.benchmark_index if p.on in (MON, THU)]
        assert starts == [D("100"), D("100")]


def test_no_runs_is_nothing_to_compare_and_says_missing() -> None:
    comparison = compare_to_benchmark(
        Ledger().engine, (), window_return=None, benchmark_key="world"
    )
    assert comparison.runs == ()
    assert comparison.span == "missing"

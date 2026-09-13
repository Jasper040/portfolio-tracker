"""The portfolio's time-weighted return, over hand-built valuation days.

M6a section 8. No database: `portfolio_returns` is pure over a sequence of
`ValuationPoint`s and a flow mapping, so every figure below is computed by hand
and asserted exactly. `tests/integration/test_portfolio_return.py` drives the
same function through `rebuild()`, which is where the claims about trades live.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

from app.analytics.portfolio_return import BASIS, DIVIDENDS, LANE, portfolio_returns
from app.analytics.quotes import FULL, MANUAL, MISSING, PARTIAL
from app.analytics.valuation import ValuationPoint
from app.models.types import Coverage

MON = date(2025, 3, 3)
TUE = date(2025, 3, 4)
WED = date(2025, 3, 5)
THU = date(2025, 3, 6)
FRI = date(2025, 3, 7)
SAT = date(2025, 3, 8)
NEXT_MON = date(2025, 3, 10)


def day(on: date, value: str | None, coverage: Coverage = FULL) -> ValuationPoint:
    """One close. Only `value_base` and `coverage` are read; the rest is filled
    so the point is a real `ValuationPoint` rather than a lookalike."""
    if value is None:
        return ValuationPoint(
            on=on, holdings_base=None, cash_base=D("0.00"), value_base=None,
            coverage=MISSING, covered_pct=None,
        )
    return ValuationPoint(
        on=on, holdings_base=D("0.00"), cash_base=D(value), value_base=D(value),
        coverage=coverage, covered_pct=D("1"),
    )


class TestFlows:
    def test_a_deposit_into_a_flat_market_returns_zero(self) -> None:
        """The load-bearing test (M6a section 8), written and failed first.
        Prices flat, a deposit of any size: zero that day and every day after.
        It fails for a sign error, for the flow left in the numerator, for the
        flow added to the denominator, and for a flow dated to the wrong side
        of the close."""
        points = [
            day(MON, "1000.00"), day(TUE, "1000.00"),
            day(WED, "6000.00"), day(THU, "6000.00"),
        ]
        result = portfolio_returns(points, {WED: D("5000.00")})
        assert [step.daily_return for step in result.links] == [D("0"), D("0"), D("0")]
        assert result.linked_return == D("0")

    def test_a_withdrawal_from_a_flat_market_returns_zero(self) -> None:
        points = [day(MON, "1000.00"), day(TUE, "600.00")]
        result = portfolio_returns(points, {TUE: D("-400.00")})
        assert result.links[0].daily_return == D("0")

    def test_a_deposit_earns_nothing_on_the_day_it_arrives(self) -> None:
        """M6a-5: flows are effective at the close. 1000 rises 10% on the day 500
        arrives: the return is 10% of what was there BEFORE, not 100/1500."""
        points = [day(MON, "1000.00"), day(TUE, "1600.00")]
        result = portfolio_returns(points, {TUE: D("500.00")})
        assert result.links[0].daily_return == D("0.1")

    def test_a_weekend_deposit_belongs_to_the_next_weekday(self) -> None:
        """P-1. `cash_daily` applies a Saturday row to Monday's balance, so
        Monday's close already holds the deposit. Looked up by exact date, it
        would be missed and read as Monday's gain."""
        points = [day(FRI, "1000.00"), day(NEXT_MON, "1500.00")]
        result = portfolio_returns(points, {SAT: D("500.00")})
        assert result.links[0].flow_base == D("500.00")
        assert result.links[0].daily_return == D("0")

    def test_a_flow_on_or_before_the_first_close_is_in_no_link(self) -> None:
        """It is inside `V(start)` already; subtracting it again counts it twice."""
        points = [day(MON, "1000.00"), day(TUE, "1000.00")]
        result = portfolio_returns(
            points, {date(2025, 2, 28): D("50.00"), MON: D("1000.00")}
        )
        assert result.links[0].flow_base == D("0.00")
        assert result.links[0].daily_return == D("0")


class TestLinking:
    def test_with_no_flows_the_linked_figure_is_the_plain_ratio(self) -> None:
        """M6a section 8. Chain-linking earns its complexity only where flows
        exist; this pins that it costs nothing where they do not."""
        points = [
            day(MON, "1000.00"), day(TUE, "1250.00"),
            day(WED, "1000.00"), day(THU, "1500.00"),
        ]
        result = portfolio_returns(points, {})
        assert result.linked_return == D("1500.00") / D("1000.00") - 1

    def test_doubling_every_amount_leaves_every_daily_return_unchanged(self) -> None:
        """Scale invariance: what distinguishes TWR from MWR, and why parent doc
        Sec 7.4 compares only TWR against a benchmark."""
        days = (MON, TUE, WED, THU)
        values = ("1000.00", "1100.00", "1600.00", "1540.00")
        flows = {WED: D("500.00")}
        once = portfolio_returns([day(d, v) for d, v in zip(days, values, strict=True)], flows)
        twice = portfolio_returns(
            [day(d, str(D(v) * 2)) for d, v in zip(days, values, strict=True)],
            {on: amount * 2 for on, amount in flows.items()},
        )
        assert [s.daily_return for s in twice.links] == [s.daily_return for s in once.links]
        assert twice.linked_return == once.linked_return

    def test_the_index_starts_at_100_and_follows_the_links(self) -> None:
        points = [day(MON, "1000.00"), day(TUE, "1100.00"), day(WED, "1210.00")]
        (run,) = portfolio_returns(points, {}).runs
        assert [(p.on, p.index) for p in run.index] == [
            (MON, D("100")), (TUE, D("110")), (WED, D("121")),
        ]

    def test_the_result_is_labelled(self) -> None:
        """Parent doc Sec 7.4: no unlabelled return. The labels ride on the object."""
        result = portfolio_returns([day(MON, "1000.00"), day(TUE, "1000.00")], {})
        assert (result.basis, result.lane, result.dividends) == (BASIS, LANE, DIVIDENDS)
        assert BASIS == "time_weighted"


class TestGaps:
    def _gapped(self) -> list[ValuationPoint]:
        return [
            day(MON, "1000.00"), day(TUE, "1100.00"), day(WED, None),
            day(THU, "1200.00"), day(FRI, "1260.00"),
        ]

    def test_one_unpriceable_day_breaks_two_links_and_withholds_the_figure(self) -> None:
        """M6a section 8, asserted as an absence and never as a zero."""
        result = portfolio_returns(self._gapped(), {})
        assert [s.daily_return for s in result.links] == [D("0.1"), None, None, D("0.05")]
        assert len(result.runs) == 2
        assert result.linked_return is None
        assert result.gaps == 1
        assert "no single figure spans a gap" in (result.reason or "")

    def test_each_run_keeps_its_own_figure(self) -> None:
        first, second = portfolio_returns(self._gapped(), {}).runs
        assert (first.start, first.end, first.linked_return) == (MON, TUE, D("0.1"))
        assert (second.start, second.end, second.linked_return) == (THU, FRI, D("0.05"))

    def test_a_broken_link_names_the_day_with_no_valuation(self) -> None:
        result = portfolio_returns(self._gapped(), {})
        assert result.links[1].reason == "no valuation on 2025-03-05"
        assert result.links[2].reason == "no valuation on 2025-03-05"

    def test_an_unpriceable_first_day_withholds_the_window_figure_too(self) -> None:
        """P-3. Reporting the later run as the window's return would be a return
        over a span nobody asked for."""
        points = [day(MON, None), day(TUE, "1000.00"), day(WED, "1100.00")]
        result = portfolio_returns(points, {})
        assert len(result.runs) == 1
        assert result.linked_return is None
        assert result.gaps == 1

    def test_a_gap_is_counted_once_however_long_it_lasts(self) -> None:
        points = [day(MON, "1000.00"), day(TUE, None), day(WED, None), day(THU, "1000.00")]
        result = portfolio_returns(points, {})
        assert result.gaps == 1
        assert result.runs == ()
        assert result.reason == "no link in this window could be measured"


class TestCapital:
    def test_days_before_the_first_capital_are_not_part_of_the_series(self) -> None:
        """M6a section 3.4: before the first deposit there is no return to
        measure, which is a different statement from a return of zero."""
        points = [
            day(MON, "0.00"), day(TUE, "0.00"),
            day(WED, "1000.00"), day(THU, "1100.00"),
        ]
        result = portfolio_returns(points, {WED: D("1000.00")})
        assert result.start == WED
        assert [s.on for s in result.links] == [THU]
        assert result.linked_return == D("0.1")

    def test_an_emptied_account_breaks_the_run_with_its_own_reason(self) -> None:
        """P-2. A figure chained across a stretch with no capital in it would be
        as much an invention as one chained across a stretch with no prices."""
        points = [
            day(MON, "1000.00"), day(TUE, "0.00"),
            day(WED, "500.00"), day(THU, "550.00"),
        ]
        result = portfolio_returns(points, {TUE: D("-1000.00"), WED: D("500.00")})
        assert [s.daily_return for s in result.links] == [D("0"), None, D("0.1")]
        assert result.links[1].reason == "no capital at the close of 2025-03-04"
        assert result.linked_return is None
        assert result.gaps == 1

    def test_one_close_is_nothing_to_measure_and_says_missing(self) -> None:
        """No observations is not a well-covered set of them."""
        result = portfolio_returns([day(MON, "1000.00")], {})
        assert result.links == ()
        assert result.linked_return is None
        assert result.coverage == MISSING
        assert result.reason is not None

    def test_an_empty_window_measures_nothing(self) -> None:
        result = portfolio_returns([], {})
        assert result.start is None
        assert result.coverage == MISSING


class TestCoverage:
    def test_a_link_carries_the_worse_of_its_two_closes(self) -> None:
        points = [day(MON, "1000.00"), day(TUE, "1000.00", PARTIAL), day(WED, "1000.00")]
        assert [s.coverage for s in portfolio_returns(points, {}).links] == [PARTIAL, PARTIAL]

    def test_a_run_reports_the_worst_close_it_was_built_from(self) -> None:
        """A figure built from stale or hand-typed prices says so (M6a section 4)."""
        points = [day(MON, "1000.00", MANUAL), day(TUE, "1000.00"), day(WED, "1000.00")]
        (run,) = portfolio_returns(points, {}).runs
        assert run.coverage == MANUAL

    def test_the_window_reports_its_worst_day(self) -> None:
        points = [day(MON, "1000.00"), day(TUE, None), day(WED, "1000.00")]
        assert portfolio_returns(points, {}).coverage == MISSING

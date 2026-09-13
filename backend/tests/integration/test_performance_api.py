"""What `GET /api/performance` puts on the wire (M6a).

Driven through `rebuild()` on the synthetic ledger, so every figure below is the
same arithmetic the analytics tests pin, reached the way a reader reaches it.

The ledger: 1000.00 deposited the Friday before, 10 shares bought at Monday's
close of 20.00, up 10% on Wednesday and flat after. That is 0.02 over the
window. The benchmark goes from 50.00 to 50.50 over the same days: 0.01.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal as D

from fastapi.testclient import TestClient

from app.ingest.benchmarks import Benchmark
from app.main import create_app
from tests.integration.synthetic_ledger import OTHER, Ledger

PREV_FRI = date(2025, 2, 28)
MON = date(2025, 3, 3)
TUE = date(2025, 3, 4)
WED = date(2025, 3, 5)
THU = date(2025, 3, 6)
FRI = date(2025, 3, 7)

WORLD = Benchmark(
    key="world", symbol="AAA.XX", currency="EUR", name="A world proxy", ter=D("0.20")
)


def _exactly(text: object, expected: str) -> bool:
    """A return on the wire is a string, and its VALUE is exact. Its spelling
    is not asserted: `str(Decimal)` keeps trailing zeros that accumulate
    through multiplication (1.00 x 1.02 is 1.0200), and nothing reads them."""
    return isinstance(text, str) and D(text) == D(expected)


def _ledger(*, gap: bool = False) -> Ledger:
    """With `gap`, a second instrument bought on Wednesday has no close until
    Thursday, so Wednesday cannot be valued."""
    ledger = Ledger().deposit(PREV_FRI, "1000.00").buy(MON, "10", "20.00")
    closes = ((MON, "20.00"), (TUE, "20.00"), (WED, "22.00"), (THU, "22.00"), (FRI, "22.00"))
    for on, close in closes:
        ledger.close(on, close)
    benchmarks = ((MON, "50.00"), (TUE, "50.00"), (WED, "50.50"), (THU, "50.50"), (FRI, "50.50"))
    for on, close in benchmarks:
        ledger.benchmark(on, close)
    if gap:
        ledger.buy(WED, "1", "10.00", isin=OTHER).close(THU, "10.00", isin=OTHER)
    return ledger


def _client(ledger: Ledger) -> TestClient:
    return TestClient(create_app(engine=ledger.rebuilt(through=FRI), benchmarks=(WORLD,)))


class TestEnvelope:
    def test_reports_no_lot_method_because_none_was_applied(self) -> None:
        """TWR is built from share counts, closes and cash, and does not change
        with the lot method. Naming one would claim a computation that never ran."""
        assert _client(_ledger()).get("/api/performance").json()["method"] is None

    def test_labels_the_portfolio_figure(self) -> None:
        body = _client(_ledger()).get("/api/performance").json()
        assert body["basis"] == "time_weighted"
        assert body["lane"] == "unadjusted_close_plus_cash"
        assert body["dividends"] == "held_as_cash_net_of_withholding"

    def test_carries_the_worst_coverage_and_the_base_currency(self) -> None:
        body = _client(_ledger()).get("/api/performance").json()
        assert body["coverage"] == "full"
        assert body["base_currency"] == "EUR"


class TestTheFigure:
    def test_the_window_figure_crosses_the_wire_as_a_string(self) -> None:
        body = _client(_ledger()).get("/api/performance").json()
        assert _exactly(body["linked_return"], "0.02")
        assert body["gaps"] == 0
        assert body["reason"] is None

    def test_every_link_carries_its_flow_as_a_string(self) -> None:
        body = _client(_ledger().deposit(WED, "500.00")).get("/api/performance").json()
        wednesday = next(step for step in body["links"] if step["date"] == WED.isoformat())
        assert wednesday["flow_base"] == "500.00"
        assert _exactly(wednesday["daily_return"], "0.02")
        assert _exactly(body["linked_return"], "0.02")

    def test_the_index_is_on_the_wire_from_the_first_close(self) -> None:
        body = _client(_ledger()).get("/api/performance").json()
        assert body["portfolio_index"][0] == {"date": PREV_FRI.isoformat(), "index": "100"}


class TestTheWindow:
    def test_the_default_window_starts_at_the_ledgers_first_day(self) -> None:
        """P-4. Not the first position: the day money first buys shares earns or
        loses the gap between its price and the close."""
        body = _client(_ledger()).get("/api/performance").json()
        assert body["start"] == PREV_FRI.isoformat()
        assert body["links"][0]["date"] == MON.isoformat()
        assert body["requested_from"] is None
        assert body["clamped"] is False

    def test_an_explicit_start_is_honoured(self) -> None:
        body = _client(_ledger()).get(f"/api/performance?from={TUE}").json()
        assert body["start"] == TUE.isoformat()
        assert [step["date"] for step in body["links"]] == [
            WED.isoformat(), THU.isoformat(), FRI.isoformat(),
        ]
        assert _exactly(body["linked_return"], "0.02")
        assert body["clamped"] is False

    def test_a_start_before_the_ledger_is_clamped_and_says_so(self) -> None:
        five_years_back = (PREV_FRI - timedelta(days=365 * 5)).isoformat()
        body = _client(_ledger()).get(f"/api/performance?from={five_years_back}").json()
        assert body["requested_from"] == five_years_back
        assert body["clamped"] is True
        assert body["start"] == PREV_FRI.isoformat()

    def test_refuses_a_window_that_ends_before_it_starts(self) -> None:
        response = _client(_ledger()).get(f"/api/performance?from={TUE}&to={MON}")
        assert response.status_code == 422

    def test_refuses_a_date_it_cannot_read(self) -> None:
        assert _client(_ledger()).get("/api/performance?from=03-03-2025").status_code == 422


class TestGaps:
    def test_a_gap_sends_null_with_a_reason_and_keeps_both_runs(self) -> None:
        body = _client(_ledger(gap=True)).get("/api/performance").json()
        assert body["linked_return"] is None
        assert body["gaps"] == 1
        assert "no single figure spans a gap" in body["reason"]
        wednesday = next(step for step in body["links"] if step["date"] == WED.isoformat())
        assert wednesday["daily_return"] is None
        assert wednesday["reason"] == f"no valuation on {WED.isoformat()}"
        assert len(body["runs"]) == 2
        assert body["coverage"] == "missing"


class TestBenchmark:
    def test_no_benchmark_asked_for_means_no_comparison(self) -> None:
        assert _client(_ledger()).get("/api/performance").json()["comparison"] is None

    def test_an_unknown_benchmark_is_a_422_naming_the_configured_set(self) -> None:
        response = _client(_ledger()).get("/api/performance?benchmark=nope")
        assert response.status_code == 422
        assert "world" in response.json()["detail"]

    def test_the_comparison_is_labelled_on_the_benchmark_side(self) -> None:
        comparison = _client(_ledger()).get("/api/performance?benchmark=world").json()["comparison"]
        assert comparison["basis"] == "total_return"
        assert comparison["dividends"] == "reinvested_gross"

    def test_the_figures_cross_the_wire_as_strings(self) -> None:
        comparison = _client(_ledger()).get("/api/performance?benchmark=world").json()["comparison"]
        assert _exactly(comparison["benchmark_return"], "0.01")
        assert _exactly(comparison["excess"], "0.01")
        assert comparison["span"] == "full"
        (row,) = comparison["runs"]
        assert _exactly(row["portfolio_return"], "0.02")
        assert _exactly(row["excess"], "0.01")
        assert row["reason"] is None

    def test_across_a_gap_every_run_is_compared_and_the_window_is_not(self) -> None:
        response = _client(_ledger(gap=True)).get("/api/performance?benchmark=world")
        comparison = response.json()["comparison"]
        assert len(comparison["runs"]) == 2
        assert comparison["excess"] is None

"""The instrument chart endpoints (M3 Task 7).

Two things are tested here that no analytics unit test can reach: the wire
envelope (`method: null`, every `Decimal` as a string, an `IntervalExcess`'s
`reason` actually reaching the JSON) and the route's own judgement calls --
window sizing, unknown-ISIN vs. unknown-benchmark error codes, and drawing a
chart with no benchmark at all.

Dates are relative to `date.today()`, not hardcoded, purely so the fixtures
stay inside whichever `range` window they are meant to exercise no matter when
the suite runs. The route itself does NOT read the wall clock: it anchors a
chart's right edge on the instrument's own newest `price_daily` row (same
convention as `analytics/valuation.py`'s `window_end = end or
cash_rows[-1].cash_date`, one level down) so a lagging price fetch never turns
into a run of `close_base: null` days at the end of the line.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal as D
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import create_engine_and_tables
from app.domain.positions import weekdays
from app.ingest.benchmarks import Benchmark
from app.main import create_app
from app.models.ledger import Account, ImportBatch, PositionDaily, Transaction
from app.models.market import BenchmarkDaily, PriceDaily

ZERO = D("0.00")
FETCHED = datetime(2026, 9, 6, 12, 0, 0)

TODAY = date.today()
#: Well inside every window, so the price line always has a priced point
#: regardless of when the suite runs.
HELD_START = TODAY - timedelta(days=30)
HELD_END = TODAY - timedelta(days=5)
#: Older than any `1Y` window (365 days) can reach -- the whole point of the
#: `range=max` test.
OLD_TRADE = TODAY - timedelta(days=900)

ISIN = "XX0000000001"  # invented; no holding of anyone's
NEVER_TRADED = "XX0000000002"  # invented; never appears in any fixture below

WORLD = Benchmark(
    key="world", symbol="AAA.XX", currency="EUR", name="A world proxy", ter=D("0.20")
)
EUROPE = Benchmark(
    key="europe", symbol="BBB.XX", currency="EUR", name="A europe proxy", ter=D("0.12")
)
BENCHMARKS = (WORLD, EUROPE)


class Seed:
    """A tiny world: whichever rows a test needs, and nothing else.

    Modeled on `tests/integration/test_instrument_price.py`'s builder.
    """

    def __init__(self, engine) -> None:
        self.engine = engine
        with Session(engine) as session:
            if session.exec(select(Account)).first() is None:
                session.add(
                    Account(id=uuid4(), broker="degiro", name="test", base_currency="EUR")
                )
                session.commit()

    def traded(
        self,
        on: date,
        isin: str = ISIN,
        *,
        quantity: str = "10",
        txn_type: str = "BUY",
        is_economic: bool = True,
        product_name: str = "Example",
    ) -> "Seed":
        batch_id = uuid4()
        with Session(self.engine) as session:
            session.add(
                ImportBatch(
                    id=batch_id, source="degiro", filename="t.csv", file_sha256="0" * 64,
                    parser_version="1", imported_at=FETCHED, row_count=1, inserted_count=1,
                )
            )
            account = session.exec(select(Account)).first()
            account_id = account.id  # type: ignore[union-attr]
            session.add(
                Transaction(
                    id=uuid4(), account_id=account_id, import_batch_id=batch_id,
                    source="degiro", source_ref=f"n-{uuid4()}", txn_type=txn_type,
                    trade_date=on, isin=isin, product_name=product_name, quantity=D(quantity),
                    price_local=D("10.00"), currency_local="EUR", fee_base=ZERO,
                    tax_base=ZERO, autofx_fee_base=ZERO, value_base=D("-100.00"),
                    net_base=D("-100.00"), raw_json="{}", is_economic=is_economic,
                )
            )
            session.commit()
        return self

    def held_over(
        self, start: date, end: date, isin: str = ISIN, *, quantity: str = "10"
    ) -> "Seed":
        with Session(self.engine) as session:
            for day in weekdays(start, end):
                session.add(
                    PositionDaily(id=uuid4(), position_date=day, isin=isin, quantity=D(quantity))
                )
            session.commit()
        return self

    def priced(self, on: date, isin: str = ISIN, close: str = "20.00") -> "Seed":
        with Session(self.engine) as session:
            session.add(
                PriceDaily(
                    id=uuid4(), isin=isin, price_date=on, close_unadjusted=D(close),
                    # Deliberately different, so a join that read the wrong column
                    # would produce a wrong number rather than the same one.
                    close_adjusted=D(close) / 2, currency="EUR", source="yahoo",
                    fetched_at=FETCHED,
                )
            )
            session.commit()
        return self

    def benchmarked(self, on: date, key: str, close: str = "50.00") -> "Seed":
        with Session(self.engine) as session:
            session.add(
                BenchmarkDaily(
                    id=uuid4(), key=key, price_date=on, close_unadjusted=D(close) * 2,
                    close_adjusted=D(close), currency="EUR", source="yahoo",
                    fetched_at=FETCHED,
                )
            )
            session.commit()
        return self


def _basic_client(*, benchmarks: tuple[Benchmark, ...] = BENCHMARKS) -> TestClient:
    """One instrument, held recently, with a priced line -- no benchmark data."""
    engine = create_engine_and_tables("sqlite://")
    seed = Seed(engine)
    seed.traded(HELD_START)
    seed.held_over(HELD_START, HELD_END)
    seed.priced(HELD_START)
    seed.priced(HELD_END, close="22.00")
    return TestClient(create_app(engine=engine, benchmarks=benchmarks))


class TestChartEnvelope:
    def test_reports_no_lot_method_because_none_was_applied(self) -> None:
        """Share counts and closes are method-independent -- naming FIFO here
        would claim a computation that never ran. Same reasoning as
        `ValuationSeriesOut`."""
        client = _basic_client()
        body = client.get(f"/api/instruments/{ISIN}/chart").json()
        assert body["method"] is None

    def test_carries_a_coverage(self) -> None:
        client = _basic_client()
        body = client.get(f"/api/instruments/{ISIN}/chart").json()
        assert body["coverage"] in ("missing", "partial", "manual", "full")

    def test_carries_the_isin(self) -> None:
        client = _basic_client()
        body = client.get(f"/api/instruments/{ISIN}/chart").json()
        assert body["isin"] == ISIN


class TestUnknownIsin:
    def test_is_a_404_not_an_empty_chart(self) -> None:
        client = _basic_client()
        resp = client.get(f"/api/instruments/{NEVER_TRADED}/chart")
        assert resp.status_code == 404


class TestUnknownBenchmark:
    def test_is_a_422_naming_the_configured_set(self) -> None:
        client = _basic_client()
        resp = client.get(f"/api/instruments/{ISIN}/chart?benchmark=nope")
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "world" in detail
        assert "europe" in detail


class TestOmittingBenchmark:
    def test_comparison_is_null_and_the_chart_still_draws(self) -> None:
        client = _basic_client()
        body = client.get(f"/api/instruments/{ISIN}/chart").json()
        assert body["comparison"] is None
        assert len(body["points"]) > 0


class TestRangeWindow:
    def test_max_reaches_further_back_than_1y(self) -> None:
        engine = create_engine_and_tables("sqlite://")
        seed = Seed(engine)
        seed.traded(OLD_TRADE)
        # A recent priced day anchors the window's right edge (see the module
        # docstring). Without one, both ranges fall back to `OLD_TRADE` itself
        # -- `max` degenerates to a single day and the invariant below is
        # vacuous rather than exercised.
        seed.priced(HELD_END)
        client = TestClient(create_app(engine=engine, benchmarks=BENCHMARKS))

        one_year = client.get(f"/api/instruments/{ISIN}/chart?range=1Y").json()
        maximum = client.get(f"/api/instruments/{ISIN}/chart?range=max").json()

        one_year_start = date.fromisoformat(one_year["points"][0]["date"])
        max_start = date.fromisoformat(maximum["points"][0]["date"])
        assert max_start < one_year_start

    def test_an_unconfigured_range_is_refused(self) -> None:
        client = _basic_client()
        resp = client.get(f"/api/instruments/{ISIN}/chart?range=10Y")
        assert resp.status_code == 422


class TestWindowAnchor:
    def test_the_last_point_is_priced_not_a_run_of_nulls(self) -> None:
        """`_basic_client` prices the instrument only up to `HELD_END`, five
        days before `TODAY` (the wall clock at suite-run time) -- exactly the
        lag Fix 1 exists for. The window's right edge must follow the data:
        anchoring on `date.today()` instead would put the last five days of
        the default `range=1Y` window past any price, and the chart would end
        in a run of `close_base: null` rather than on the last priced day.
        """
        client = _basic_client()
        body = client.get(f"/api/instruments/{ISIN}/chart").json()

        last_point = body["points"][-1]
        assert last_point["date"] == HELD_END.isoformat()
        assert last_point["close_base"] is not None


class TestMoneyOnTheWire:
    def test_every_money_field_is_a_string(self) -> None:
        client = _basic_client()
        body = client.get(f"/api/instruments/{ISIN}/chart").json()

        priced_points = [p for p in body["points"] if p["close_base"] is not None]
        assert priced_points
        assert isinstance(priced_points[0]["close_base"], str)

        priced_intervals = [i for i in body["intervals"] if i["price_return"] is not None]
        assert priced_intervals
        assert isinstance(priced_intervals[0]["price_return"], str)

        assert body["markers"], "the seeded BUY must produce a marker"
        marker = body["markers"][0]
        for field in ("quantity", "price", "fees", "position_after"):
            assert isinstance(marker[field], str)

    def test_comparison_money_fields_are_strings(self) -> None:
        engine = create_engine_and_tables("sqlite://")
        seed = Seed(engine)
        seed.traded(HELD_START)
        seed.held_over(HELD_START, HELD_END)
        for day in weekdays(HELD_START, HELD_END):
            seed.priced(day)
            seed.benchmarked(day, "world")
        client = TestClient(create_app(engine=engine, benchmarks=BENCHMARKS))

        body = client.get(f"/api/instruments/{ISIN}/chart?benchmark=world").json()
        comparison = body["comparison"]
        assert comparison is not None
        assert comparison["instrument_index"], "a full benchmark should yield an index"
        assert isinstance(comparison["instrument_index"][0]["index"], str)
        assert isinstance(comparison["benchmark_index"][0]["index"], str)
        row = comparison["intervals"][0]
        # This fixture is a fully covered interval on both sides -- unlike
        # `TestExcessReason` below -- so all three fields must actually be
        # populated. `None or isinstance(..., str)` would pass even if a
        # regression dropped all three to `None`.
        for field in ("instrument_return", "benchmark_return", "excess"):
            assert row[field] is not None
            assert isinstance(row[field], str)


class TestExcessReason:
    def test_a_reason_reaches_the_wire_when_excess_is_null(self) -> None:
        """The instrument's own span shortfall never becomes a coverage badge
        (by design -- see `Comparison.coverage`'s docstring). It surfaces only
        in `reason`, and Task 5's implementer flagged this specifically: if the
        API drops the field, that "this figure is meaningless" case becomes
        invisible to the reader again.
        """
        engine = create_engine_and_tables("sqlite://")
        seed = Seed(engine)
        seed.traded(HELD_START)
        seed.held_over(HELD_START, HELD_END)
        for day in weekdays(HELD_START, HELD_END):
            seed.priced(day)
        # Deliberately no `benchmarked(...)` calls: the benchmark has no data
        # at all over the holding, so its coverage is `missing` and the excess
        # for that interval must come back `None` with a `reason`.
        client = TestClient(create_app(engine=engine, benchmarks=BENCHMARKS))

        body = client.get(f"/api/instruments/{ISIN}/chart?benchmark=world").json()
        comparison = body["comparison"]
        assert comparison is not None
        assert comparison["intervals"], "the holding must produce an interval row"
        row = comparison["intervals"][0]
        assert row["excess"] is None
        assert row["reason"] is not None
        assert "world" in row["reason"]


class TestInstrumentList:
    """`GET /api/instruments` (M3 Task 9 fix-round): the picker's candidate
    list must reach every instrument the ledger ever traded, not only the ones
    with an open position today -- an instrument fully exited is the clearest
    "out of market" case M3's whole feature exists to show.
    """

    def test_lists_an_instrument_that_has_since_been_fully_closed(self) -> None:
        """No `PositionDaily` row is seeded at all: as far as `/api/positions`
        is concerned this ISIN was never open. The BUY-then-SELL pair leaves
        it at quantity zero today, which is exactly the case `fetchPositions`
        could never surface and this endpoint must.
        """
        engine = create_engine_and_tables("sqlite://")
        seed = Seed(engine)
        seed.traded(HELD_START, ISIN, txn_type="BUY", product_name="Closed Fund")
        seed.traded(HELD_END, ISIN, txn_type="SELL", product_name="Closed Fund")
        client = TestClient(create_app(engine=engine, benchmarks=BENCHMARKS))

        body = client.get("/api/instruments").json()
        by_isin = {item["isin"]: item for item in body["items"]}
        assert ISIN in by_isin
        assert by_isin[ISIN]["product_name"] == "Closed Fund"

    def test_excludes_non_economic_legs(self) -> None:
        """The same filter `_markers` applies in `analytics/instrument_price.py`,
        and for the same reason: a corporate action's legs are not real trades,
        and without this clause they would conjure a phantom instrument that
        was never actually bought or sold.
        """
        split_only = "XX0000000009"  # invented; appears only as a split leg
        engine = create_engine_and_tables("sqlite://")
        seed = Seed(engine)
        seed.traded(HELD_START, split_only, is_economic=False, product_name="Split Leg")
        client = TestClient(create_app(engine=engine, benchmarks=BENCHMARKS))

        body = client.get("/api/instruments").json()
        isins = {item["isin"] for item in body["items"]}
        assert split_only not in isins

    def test_carries_the_provenance_envelope(self) -> None:
        """`method` is `None` for the same reason it is on the chart: a
        distinct-ISIN listing has no lot method to name."""
        client = _basic_client()
        body = client.get("/api/instruments").json()
        assert body["method"] is None
        assert body["coverage"] in ("missing", "partial", "manual", "full")


class TestBenchmarkList:
    def test_returns_key_name_and_ter_never_the_symbol(self) -> None:
        client = _basic_client()
        body = client.get("/api/benchmarks").json()
        keys = {item["key"] for item in body["items"]}
        assert keys == {"world", "europe"}
        for item in body["items"]:
            assert set(item) == {"key", "name", "ter"}
            assert isinstance(item["ter"], str)

    def test_an_empty_configured_set_is_an_empty_list_not_an_error(self) -> None:
        client = _basic_client(benchmarks=())
        body = client.get("/api/benchmarks").json()
        assert body["items"] == []

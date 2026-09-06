"""What the two M2 endpoints put on the wire.

Two things are tested here that no unit test can reach. Money must arrive as a
STRING -- a JSON number is an IEEE double and would undo the exactness the
Decimal storage exists for -- and `null` must arrive as `null` rather than as
"0", because parent doc Sec 8.1's whole rule is that an unpriceable day says so
instead of reporting nothing as zero.

The envelopes disagree about `method` on purpose. The value series ran no lot
matching, so `null` is the honest answer; the positions table read a cost basis
out of `lot`, so it must name the method it read.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import create_engine_and_tables
from app.main import create_app
from app.models.ledger import (
    Account,
    CashDaily,
    ImportBatch,
    Lot,
    PositionDaily,
    Transaction,
)
from app.models.market import FxDaily, PriceDaily

D = Decimal
ZERO = D("0.00")
FETCHED = datetime(2026, 9, 6, 12, 0, 0)

MON = date(2025, 3, 3)
TUE = date(2025, 3, 4)
A = "NL0000000001"
B = "US0000000404"

def seed(engine, *, price_b: bool = True) -> None:
    account_id, batch_id = uuid4(), uuid4()
    with Session(engine) as session:
        session.add(Account(id=account_id, broker="degiro", name="t", base_currency="EUR"))
        session.add(
            ImportBatch(
                id=batch_id, source="degiro", filename="t.csv", file_sha256="0" * 64,
                parser_version="1", imported_at=FETCHED, row_count=2, inserted_count=2,
            )
        )
        for isin, name, currency in ((A, "Example Holdings", "EUR"), (B, "Other Holdings", "USD")):
            session.add(
                Transaction(
                    id=uuid4(), account_id=account_id, import_batch_id=batch_id,
                    source="degiro", source_ref=f"ref-{isin}", txn_type="BUY",
                    trade_date=MON, isin=isin, product_name=name, quantity=D("1"),
                    price_local=D("1"), currency_local=currency, fee_base=ZERO,
                    tax_base=ZERO, autofx_fee_base=ZERO, value_base=D("-1"),
                    net_base=D("-1"), raw_json="{}",
                )
            )
        for day in (MON, TUE):
            session.add(CashDaily(id=uuid4(), cash_date=day, balance_base=D("-50.00")))
            session.add(
                PositionDaily(id=uuid4(), position_date=day, isin=A, quantity=D("10"))
            )
            session.add(
                PositionDaily(id=uuid4(), position_date=day, isin=B, quantity=D("5"))
            )
            session.add(
                PriceDaily(
                    id=uuid4(), isin=A, price_date=day, close_unadjusted=D("20.00"),
                    close_adjusted=D("10.00"), currency="EUR", source="yahoo",
                    fetched_at=FETCHED,
                )
            )
            if price_b:
                session.add(
                    PriceDaily(
                        id=uuid4(), isin=B, price_date=day, close_unadjusted=D("20.80"),
                        close_adjusted=D("20.80"), currency="USD", source="yahoo",
                        fetched_at=FETCHED,
                    )
                )
                session.add(
                    FxDaily(
                        id=uuid4(), from_ccy="USD", to_ccy="EUR", rate_date=day,
                        rate=D("1.04"), source="ecb", fetched_at=FETCHED,
                    )
                )
        for method, cost in (("FIFO", "150.00"), ("HIFO", "180.00")):
            session.add(
                Lot(
                    id=uuid4(), method=method, isin=A, source_ref=f"lot-{method}",
                    opened_on=MON, quantity=D("10"), price=D(cost) / D("10"),
                    cost_basis=D(cost), commission=D("2.00"), autofx=ZERO, tax=ZERO,
                )
            )
            session.add(
                Lot(
                    id=uuid4(), method=method, isin=B, source_ref=f"lotb-{method}",
                    opened_on=MON, quantity=D("5"), price=D("16.00"),
                    cost_basis=D("80.00"), commission=ZERO, autofx=ZERO, tax=ZERO,
                )
            )
        session.commit()

@pytest.fixture(name="client")
def _client():
    engine = create_engine_and_tables("sqlite://")
    seed(engine)
    return TestClient(create_app(engine=engine))

class TestValuationEnvelope:
    def test_reports_no_lot_method_because_none_was_applied(self, client) -> None:
        """Share counts are method-independent -- that is why `position_daily`
        has no method column. Naming FIFO here would claim a computation that
        never ran."""
        body = client.get("/api/valuation").json()
        assert body["method"] is None

    def test_carries_the_worst_coverage_in_the_series(self, client) -> None:
        assert client.get("/api/valuation").json()["coverage"] == "full"

    def test_states_the_base_currency(self, client) -> None:
        assert client.get("/api/valuation").json()["base_currency"] == "EUR"

class TestValuationPoints:
    def test_money_crosses_the_wire_as_a_string(self, client) -> None:
        point = client.get("/api/valuation").json()["items"][0]
        assert point["value_base"] == "250.00"
        assert isinstance(point["cash_base"], str)

    def test_reports_the_cash_component_separately(self, client) -> None:
        """M2-4: value is net of cash, and cash is reported as its own component
        so a reader can see which half moved."""
        point = client.get("/api/valuation").json()["items"][0]
        assert point["cash_base"] == "-50.00"
        assert point["holdings_base"] == "300.00"

    def test_covered_pct_is_a_string_too(self, client) -> None:
        assert isinstance(client.get("/api/valuation").json()["items"][0]["covered_pct"], str)

    def test_an_unpriceable_day_sends_null_not_zero(self) -> None:
        engine = create_engine_and_tables("sqlite://")
        seed(engine, price_b=False)
        body = TestClient(create_app(engine=engine)).get("/api/valuation").json()
        point = body["items"][0]
        assert point["value_base"] is None
        assert point["holdings_base"] is None
        assert point["covered_pct"] is None
        assert point["coverage"] == "missing"
        assert body["coverage"] == "missing"

class TestValuationWindow:
    def test_defaults_to_the_first_day_a_position_existed(self, client) -> None:
        body = client.get("/api/valuation").json()
        assert body["start"] == MON.isoformat()
        assert body["clamped"] is False
        assert body["requested_from"] is None

    def test_honours_an_explicit_window(self, client) -> None:
        body = client.get(f"/api/valuation?from={TUE}&to={TUE}").json()
        assert [p["date"] for p in body["items"]] == [TUE.isoformat()]

    def test_a_five_year_request_on_a_short_ledger_is_clamped_and_says_so(
        self, client
    ) -> None:
        """The reader asked to look back five years. The honest answer is
        everything there is, plus a flag saying the window was shortened -- never
        five years of padded zeros, because a zero portfolio value on a day the
        account did not exist is a false claim."""
        five_years_back = (MON - timedelta(days=365 * 5)).isoformat()
        body = client.get(f"/api/valuation?from={five_years_back}").json()
        assert body["requested_from"] == five_years_back
        assert body["clamped"] is True
        assert body["start"] == MON.isoformat()
        assert len(body["items"]) == 2

    def test_refuses_a_window_that_ends_before_it_starts(self, client) -> None:
        assert client.get(f"/api/valuation?from={TUE}&to={MON}").status_code == 422

    def test_refuses_a_date_it_cannot_read(self, client) -> None:
        assert client.get("/api/valuation?from=03-03-2025").status_code == 422

class TestPositions:
    def test_requires_a_method(self, client) -> None:
        """The cost basis comes from `lot`, and the three methods disagree about
        it. Answering without being asked would put a number under a label it did
        not earn."""
        assert client.get("/api/positions").status_code == 422

    def test_reports_the_method_it_used(self, client) -> None:
        body = client.get("/api/positions?method=HIFO").json()
        assert body["method"] == "HIFO"

    def test_reads_the_cost_basis_of_that_method(self, client) -> None:
        fifo = client.get("/api/positions?method=FIFO").json()
        hifo = client.get("/api/positions?method=HIFO").json()
        assert fifo["items"][0]["cost_basis"] == "150.00"
        assert hifo["items"][0]["cost_basis"] == "180.00"

    def test_the_share_count_is_the_same_under_both(self, client) -> None:
        fifo = client.get("/api/positions?method=FIFO").json()
        hifo = client.get("/api/positions?method=HIFO").json()
        assert [i["quantity"] for i in fifo["items"]] == [i["quantity"] for i in hifo["items"]]

    def test_reports_gross_charges_and_net_as_three_figures(self, client) -> None:
        row = client.get("/api/positions?method=FIFO").json()["items"][0]
        assert row["gross_unrealised_base"] == "50.00"
        assert row["charges_base"] == "2.00"
        assert row["unrealised_base"] == "48.00"

    def test_names_the_instrument_and_says_who_priced_it(self, client) -> None:
        row = client.get("/api/positions?method=FIFO").json()["items"][0]
        assert row["product_name"] == "Example Holdings"
        assert row["source"] == "yahoo"
        assert row["price_date"] == TUE.isoformat()

    def test_withholds_the_total_when_a_position_cannot_be_priced(self) -> None:
        engine = create_engine_and_tables("sqlite://")
        seed(engine, price_b=False)
        body = TestClient(create_app(engine=engine)).get("/api/positions?method=FIFO").json()
        assert body["total_market_value_base"] is None
        assert body["total_unrealised_base"] is None
        assert body["coverage"] == "missing"
        # Cost is ledger arithmetic and stays exact.
        assert body["total_cost_basis"] == "230.00"

    def test_an_unpriceable_row_says_missing_rather_than_zero(self) -> None:
        engine = create_engine_and_tables("sqlite://")
        seed(engine, price_b=False)
        body = TestClient(create_app(engine=engine)).get("/api/positions?method=FIFO").json()
        unpriced = [row for row in body["items"] if row["isin"] == B][0]
        assert unpriced["market_value_base"] is None
        assert unpriced["coverage"] == "missing"

"""The lot endpoints (design doc Sec 9.2).

The point of these is `method`. A realised figure without the method that produced
it is not a number, it is an opinion -- FIFO, LIFO and HIFO give three different
answers from identical rows -- so the envelope carries it and the response is the
only place the UI may learn it from.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.analytics.rebuild import rebuild
from app.db import create_engine_and_tables
from app.ingest.importer import ensure_default_account, import_degiro_export
from app.main import create_app

GOLDEN = Path(__file__).parents[1] / "golden"

RESOLVE_BOTH = """\
resolutions:
  - key: NL0000000003:2025-01-17:100.00
    treatment: corporate_action
  - key: US0000000002:2025-01-18:100.00
    treatment: corporate_action
"""


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    export = tmp_path / "degiro-export"
    export.mkdir()
    shutil.copy(GOLDEN / "degiro_transactions_golden.csv", export / "Transactions.csv")
    shutil.copy(GOLDEN / "degiro_account_golden.csv", export / "Account.csv")
    answers = tmp_path / "corporate_actions.yaml"
    answers.write_text(RESOLVE_BOTH, encoding="utf-8")

    engine: Engine = create_engine_and_tables("sqlite://")
    import_degiro_export(engine, export, ensure_default_account(engine), answers)
    for method in ("FIFO", "LIFO", "HIFO"):
        rebuild(engine, method)
    return TestClient(create_app(engine=engine))


class TestProvenance:
    def test_the_envelope_names_the_method_that_produced_the_numbers(
        self, client: TestClient
    ) -> None:
        body = client.get("/api/lots", params={"method": "HIFO"}).json()
        assert body["method"] == "HIFO"
        assert body["coverage"] == "full"

    def test_an_unknown_method_is_rejected_rather_than_defaulted(
        self, client: TestClient
    ) -> None:
        """Defaulting would answer a question the caller did not ask, under a label
        saying it did."""
        assert client.get("/api/lots", params={"method": "AVERAGE"}).status_code == 422


class TestLots:
    def test_returns_only_the_requested_method(self, client: TestClient) -> None:
        body = client.get("/api/lots", params={"method": "FIFO"}).json()
        assert body["items"]
        assert all(item["method"] == "FIFO" for item in body["items"])

    def test_money_crosses_the_wire_as_strings(self, client: TestClient) -> None:
        """JSON numbers are IEEE doubles. Serialising a Decimal as one reintroduces
        exactly the drift the storage layer prevents."""
        item = client.get("/api/lots", params={"method": "FIFO"}).json()["items"][0]
        assert isinstance(item["cost_basis"], str)
        assert isinstance(item["quantity"], str)

    def test_charges_are_reported_apart_from_the_basis(self, client: TestClient) -> None:
        item = client.get("/api/lots", params={"method": "FIFO"}).json()["items"][0]
        assert {"commission", "autofx", "tax"} <= set(item)
        assert "cost_basis" in item

    def test_filters_by_isin(self, client: TestClient) -> None:
        body = client.get(
            "/api/lots", params={"method": "FIFO", "isin": "NL0000000003"}
        ).json()
        assert {item["isin"] for item in body["items"]} == {"NL0000000003"}


class TestClosures:
    def test_reports_gross_charges_and_net(self, client: TestClient) -> None:
        """The three numbers the owner asked for, straight from the rebuild."""
        items = client.get("/api/closures", params={"method": "FIFO"}).json()["items"]
        assert items
        for item in items:
            assert {"gross_pnl", "commission", "autofx", "tax", "pnl"} <= set(item)

    def test_each_method_is_served_from_its_own_stored_rows(
        self, client: TestClient
    ) -> None:
        """The golden fixture gives every instrument exactly one buy lot, so there is
        never a choice of lot to make -- FIFO, LIFO and HIFO close identical rows and
        cannot be told apart by their P&L here (that divergence is already proven on
        a genuine multi-lot fixture in `tests/unit/test_lots.py`).

        What this endpoint must still get right: it answers from the rows stored
        under the requested method, not from whatever happens to be in the table.
        An endpoint that ignored `method` and served a fixed set, or that leaked
        another method's rows into the page, would pass a same-answer check like
        `test_reports_gross_charges_and_net` above -- this is the test that would
        catch it.
        """
        for method in ("FIFO", "LIFO", "HIFO"):
            items = client.get("/api/closures", params={"method": method}).json()["items"]
            assert items
            assert all(item["method"] == method for item in items)

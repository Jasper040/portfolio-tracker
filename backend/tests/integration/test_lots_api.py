"""The lot endpoints (design doc Sec 9.2).

The point of these is `method`. A realised figure without the method that produced
it is not a number, it is an opinion -- FIFO, LIFO and HIFO give three different
answers from identical rows -- so the envelope carries it and the response is the
only place the UI may learn it from.
"""

from __future__ import annotations

import shutil
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.analytics.rebuild import rebuild
from app.db import create_engine_and_tables
from app.ingest.importer import ensure_default_account, import_degiro_export
from app.main import create_app

GOLDEN = Path(__file__).parents[1] / "golden"

D = Decimal

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
        """Sec 6.4: the basis is quantity times price and nothing else, and the three
        charges sit beside it, never inside it.

        Asserted as arithmetic rather than as key presence. A response carrying all
        four keys with the commission quietly folded into the basis would satisfy
        `{"commission", "autofx", "tax"} <= set(item)` perfectly.
        """
        items = client.get("/api/lots", params={"method": "FIFO"}).json()["items"]
        assert items
        charged = 0
        for item in items:
            assert D(item["cost_basis"]) == D(item["quantity"]) * D(item["price"])
            charges = D(item["commission"]) + D(item["autofx"]) + D(item["tax"])
            # Positive means money paid (domain/charges.py). A negative here would
            # be the sign flip having happened twice.
            assert charges >= 0, item["source_ref"]
            if charges > 0:
                charged += 1
        # At least one real lot carries a charge, so a response that zeroed all
        # three could not satisfy the arithmetic trivially.
        assert charged > 0

    def test_filters_by_isin(self, client: TestClient) -> None:
        body = client.get(
            "/api/lots", params={"method": "FIFO", "isin": "NL0000000003"}
        ).json()
        assert {item["isin"] for item in body["items"]} == {"NL0000000003"}


class TestClosures:
    def test_reports_gross_charges_and_net(self, client: TestClient) -> None:
        """The three numbers the owner asked for, and they must reconcile.

        The previous form checked only that the keys existed -- which no realistic
        bug would remove, and which duplicated the lots-side presence check above.
        What matters is that GROSS minus the three charges IS the NET on every row:
        a closure whose columns do not add up is the exact defect the closures table
        shipped with earlier on this branch, in a screen showing all five at once.
        """
        items = client.get("/api/closures", params={"method": "FIFO"}).json()["items"]
        assert items
        for item in items:
            charges = D(item["commission"]) + D(item["autofx"]) + D(item["tax"])
            assert D(item["gross_pnl"]) - charges == D(item["pnl"]), item["id"]
            # Gross P&L is the stock's own move, so the charges must not be baked
            # into it as well: quantity times the price difference, exactly.
            assert D(item["gross_pnl"]) == D(item["quantity"]) * (
                D(item["close_price"]) - D(item["open_price"])
            )

    def test_a_closure_names_both_ledger_rows_it_came_from(
        self, client: TestClient
    ) -> None:
        """A closure is a (buy, sale) pair. Serving only the buy would leave half of
        it untraceable back to the ledger, and `sale_source_ref` was written by
        every rebuild and read by nothing until it joined this schema."""
        items = client.get("/api/closures", params={"method": "FIFO"}).json()["items"]
        assert items
        for item in items:
            assert item["lot_source_ref"]
            assert item["sale_source_ref"]
            assert item["lot_source_ref"] != item["sale_source_ref"]

    def test_each_method_is_served_from_its_own_stored_rows(
        self, client: TestClient
    ) -> None:
        """The golden fixture gives every instrument exactly one buy lot, so there is
        never a choice of lot to make -- FIFO, LIFO and HIFO close identical rows and
        cannot be told apart by their P&L here. That divergence is proven where the
        data supports it: at the matcher in `tests/unit/test_lots.py`, and through
        the whole pipeline in `test_rebuild.py`'s
        `TestMethodDivergenceSurvivesThePipeline`.

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

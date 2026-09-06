from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.schemas import TransactionPage
from app.db import create_engine_and_tables
from app.ingest.importer import ensure_default_account, import_transactions_file
from app.main import create_app

GOLDEN = Path(__file__).parents[1] / "golden" / "degiro_transactions_golden.csv"


def _client() -> TestClient:
    engine = create_engine_and_tables("sqlite://")
    account_id = ensure_default_account(engine)
    import_transactions_file(engine, GOLDEN, account_id)
    return TestClient(create_app(engine))


def test_lists_transactions_newest_first() -> None:
    """Assert the whole sequence, not just its endpoints: a broken secondary sort key
    leaves the first and last rows correct while scrambling everything between them."""
    body = _client().get("/api/transactions", params={"limit": 1000}).json()
    dates = [r["trade_date"] for r in body["items"]]
    assert dates == sorted(dates, reverse=True)
    assert body["total"] == 13


def test_money_is_serialised_as_a_string_not_a_float() -> None:
    """JSON floats reintroduce the drift Decimal exists to prevent."""
    body = _client().get("/api/transactions").json()
    row = next(r for r in body["items"] if r["order_ref"] == "aaaa0003-0000-0000-0000-000000000006")
    assert row["net_base"] == "90.35"
    assert isinstance(row["net_base"], str)


def test_raw_source_row_is_available_for_inspection() -> None:
    body = _client().get("/api/transactions").json()
    assert body["items"][0]["raw"]["Total EUR"]


def test_filters_by_isin() -> None:
    body = _client().get("/api/transactions", params={"isin": "NL0000000003"}).json()
    assert body["total"] == 3


def test_paginates() -> None:
    body = _client().get("/api/transactions", params={"limit": 5, "offset": 0}).json()
    assert len(body["items"]) == 5
    assert body["total"] == 13


def test_paging_covers_every_row_exactly_once() -> None:
    """The real pagination risk is a non-total ordering: rows tying on the sort key can
    come back in a different order per request, so a page boundary silently skips one
    row and repeats another. Only walking the pages and comparing against a single full
    fetch detects that -- asserting one page's length never will."""
    client = _client()
    whole = client.get("/api/transactions", params={"limit": 1000}).json()
    expected = [r["id"] for r in whole["items"]]

    paged: list[str] = []
    for offset in range(0, whole["total"], 5):
        page = client.get(
            "/api/transactions", params={"limit": 5, "offset": offset}
        ).json()
        paged.extend(r["id"] for r in page["items"])

    assert paged == expected
    assert len(set(paged)) == len(paged)


def test_injected_engine_does_not_require_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Building the app around a supplied engine must not read the environment.

    The whole suite injects an engine, so if `create_app` reached for settings
    unconditionally every test would need a DATABASE_URL it never uses -- and the
    failure would arrive as a Pydantic ValidationError from an unrelated import.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    engine = create_engine_and_tables("sqlite://")
    assert create_app(engine).state.engine is engine


def test_cors_origins_can_be_supplied_explicitly() -> None:
    engine = create_engine_and_tables("sqlite://")
    app = create_app(engine, cors_origins=["https://example.test"])
    origins = [
        m.kwargs["allow_origins"] for m in app.user_middleware if "allow_origins" in m.kwargs
    ]
    assert origins == [["https://example.test"]]


def test_response_envelope_cannot_be_built_without_provenance() -> None:
    """Design doc Sec 9.2: the fields are structural, not conventional.

    This is the test that keeps the rule alive. A default on either field would
    let an endpoint ship without saying what produced its numbers, and the
    omission would be invisible in review.
    """
    with pytest.raises(ValidationError):
        TransactionPage(items=[], total=0, limit=10, offset=0)  # type: ignore[call-arg]


def test_provenance_rejects_a_coverage_value_outside_the_vocabulary() -> None:
    with pytest.raises(ValidationError):
        TransactionPage(
            items=[], total=0, limit=10, offset=0, method=None, coverage="probably fine"  # type: ignore[arg-type]
        )


def test_transactions_report_no_lot_method_and_full_coverage() -> None:
    """A raw ledger listing applies no matching, so `method` is null rather than the
    configured default -- naming a method here would claim a computation that never
    ran. Coverage is full because every row is broker truth, with no external series
    that could be missing."""
    body = _client().get("/api/transactions").json()
    assert body["method"] is None
    assert body["coverage"] == "full"

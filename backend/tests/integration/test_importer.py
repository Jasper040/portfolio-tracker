from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.db import create_engine_and_tables
from app.ingest.degiro.transactions_csv import MalformedRow
from app.ingest.importer import ensure_default_account, import_transactions_file, undo_batch
from app.models.ledger import Account, ImportBatch, Transaction

GOLDEN = Path(__file__).parents[1] / "golden" / "degiro_transactions_golden.csv"


def _engine() -> Engine:
    return create_engine_and_tables("sqlite://")


def test_import_inserts_every_row() -> None:
    engine = _engine()
    account_id = ensure_default_account(engine)
    result = import_transactions_file(engine, GOLDEN, account_id)
    assert result.rows_parsed == 13
    assert result.rows_inserted == 13
    with Session(engine) as s:
        assert len(s.exec(select(Transaction)).all()) == 13


def test_reimporting_the_same_file_changes_nothing() -> None:
    """Source spec section 9.6."""
    engine = _engine()
    account_id = ensure_default_account(engine)
    import_transactions_file(engine, GOLDEN, account_id)
    second = import_transactions_file(engine, GOLDEN, account_id)
    assert second.rows_inserted == 0
    assert second.rows_skipped == 13
    with Session(engine) as s:
        assert len(s.exec(select(Transaction)).all()) == 13


def test_identical_fill_rows_both_survive_import() -> None:
    engine = _engine()
    account_id = ensure_default_account(engine)
    import_transactions_file(engine, GOLDEN, account_id)
    with Session(engine) as s:
        fills = s.exec(
            select(Transaction).where(
                Transaction.order_ref == "cccc0002-0000-0000-0000-000000000005"
            )
        ).all()
    assert len(fills) == 2


def test_undo_removes_exactly_one_batch() -> None:
    engine = _engine()
    account_id = ensure_default_account(engine)
    result = import_transactions_file(engine, GOLDEN, account_id)
    removed = undo_batch(engine, result.batch_id)
    assert removed == 13
    with Session(engine) as s:
        assert s.exec(select(Transaction)).all() == []
        assert s.exec(select(ImportBatch)).all() == []


def test_batch_records_the_file_hash() -> None:
    engine = _engine()
    account_id = ensure_default_account(engine)
    result = import_transactions_file(engine, GOLDEN, account_id)
    with Session(engine) as s:
        batch = s.get(ImportBatch, result.batch_id)
    assert batch is not None
    assert len(batch.file_sha256) == 64
    assert batch.row_count == 13


def test_a_malformed_file_aborts_the_import_leaving_nothing_behind(tmp_path: Path) -> None:
    """Atomicity here is structural: parsing finishes before any Session is opened, so
    a bad row means the database is never touched. Nothing enforced that, though - a
    refactor moving the parse inside the session would allow a half-import with a
    committed batch row, and no existing test would notice. This locks it in."""
    engine = _engine()
    account_id = ensure_default_account(engine)

    lines = GOLDEN.read_text(encoding="utf-8").strip().split("\n")
    lines[5] = "06-01-2025,09:00,TRUNCATED"  # 3 columns where 17 are required
    bad = tmp_path / "malformed.csv"
    bad.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(MalformedRow):
        import_transactions_file(engine, bad, account_id)

    with Session(engine) as s:
        assert s.exec(select(Transaction)).all() == []
        assert s.exec(select(ImportBatch)).all() == []


def test_undo_of_an_unknown_batch_is_a_no_op() -> None:
    engine = _engine()
    ensure_default_account(engine)
    assert undo_batch(engine, uuid4()) == 0


def test_ensure_default_account_is_idempotent() -> None:
    """It runs on every import, so a second call must not create a second account."""
    engine = _engine()
    first = ensure_default_account(engine)
    second = ensure_default_account(engine)
    assert first == second
    with Session(engine) as s:
        assert len(s.exec(select(Account)).all()) == 1

"""Importing a whole DeGiro export as one operation.

`Transactions.csv` is authoritative for trades and `Account.csv` for everything
else (design doc Sec 6.2), so a ledger built from only the first one has no
dividends, no deposits and no fees. Both land in a single batch: an import is one
operation, and Sec 3's undo has to be able to reverse it with one id.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.db import create_engine_and_tables
from app.ingest.importer import ensure_default_account, import_degiro_export, undo_batch
from app.models.ledger import ImportBatch, Transaction

GOLDEN = Path(__file__).parents[1] / "golden"

SPLIT_KEY = "NL0000000003:2025-01-17:100.00"
PRODUCT_CHANGE_KEY = "US0000000002:2025-01-18:100.00"

RESOLVE_BOTH = f"""\
resolutions:
  - key: {SPLIT_KEY}
    treatment: corporate_action
  - key: {PRODUCT_CHANGE_KEY}
    treatment: corporate_action
"""

#: 15 trade rows plus the 15 cash-book rows that survive classification.
TOTAL_ROWS = 30


def _engine() -> Engine:
    return create_engine_and_tables("sqlite://")


@pytest.fixture
def export(tmp_path: Path) -> Path:
    directory = tmp_path / "degiro-export"
    directory.mkdir()
    shutil.copy(GOLDEN / "degiro_transactions_golden.csv", directory / "Transactions.csv")
    shutil.copy(GOLDEN / "degiro_account_golden.csv", directory / "Account.csv")
    return directory


@pytest.fixture
def resolutions(tmp_path: Path) -> Path:
    path = tmp_path / "corporate_actions.yaml"
    path.write_text(RESOLVE_BOTH, encoding="utf-8")
    return path


def _import(engine: Engine, export: Path, resolutions: Path):
    return import_degiro_export(engine, export, ensure_default_account(engine), resolutions)


class TestBothFiles:
    def test_imports_trades_and_cash_rows_together(
        self, export: Path, resolutions: Path
    ) -> None:
        engine = _engine()
        result = _import(engine, export, resolutions)
        assert result.rows_inserted == TOTAL_ROWS
        with Session(engine) as session:
            assert len(session.exec(select(Transaction)).all()) == TOTAL_ROWS

    def test_the_cash_rows_reach_the_ledger_with_their_types(
        self, export: Path, resolutions: Path
    ) -> None:
        """Without these the browser table is a trade blotter, not a ledger."""
        engine = _engine()
        _import(engine, export, resolutions)
        with Session(engine) as session:
            types = {row.txn_type for row in session.exec(select(Transaction)).all()}
        assert {"DIVIDEND", "DIVIDEND_TAX", "DEPOSIT", "WITHDRAWAL", "FEE"} <= types

    def test_the_dropped_cash_rows_never_arrive(self, export: Path, resolutions: Path) -> None:
        """A cash sweep booked as a deposit makes MWR meaningless (Sec 3.3)."""
        engine = _engine()
        _import(engine, export, resolutions)
        with Session(engine) as session:
            rows = session.exec(select(Transaction)).all()
        descriptions = [row.raw_json.casefold() for row in rows]
        assert not [text for text in descriptions if "cash sweep" in text]
        assert not [text for text in descriptions if "reservation ideal" in text]


class TestOneBatch:
    def test_both_files_land_in_a_single_batch(self, export: Path, resolutions: Path) -> None:
        engine = _engine()
        _import(engine, export, resolutions)
        with Session(engine) as session:
            batches = session.exec(select(ImportBatch)).all()
        assert len(batches) == 1
        assert batches[0].row_count == TOTAL_ROWS

    def test_the_batch_names_both_files_and_hashes_both(
        self, export: Path, resolutions: Path
    ) -> None:
        """Provenance has to identify the pair: re-running against a changed
        Account.csv is a different import even when the trades are identical."""
        engine = _engine()
        _import(engine, export, resolutions)
        with Session(engine) as session:
            batch = session.exec(select(ImportBatch)).one()
        assert "Transactions.csv" in batch.filename
        assert "Account.csv" in batch.filename
        assert len(batch.file_sha256) == 64

    def test_the_hash_changes_when_either_file_changes(
        self, export: Path, resolutions: Path, tmp_path: Path
    ) -> None:
        first_engine = _engine()
        _import(first_engine, export, resolutions)
        with Session(first_engine) as session:
            first = session.exec(select(ImportBatch)).one().file_sha256

        # Only Account.csv changes, and only in a way classification ignores.
        account = export / "Account.csv"
        account.write_text(
            account.read_text(encoding="utf-8").replace("iDEAL Deposit", "IDEAL Deposit"),
            encoding="utf-8",
        )

        second_engine = _engine()
        _import(second_engine, export, resolutions)
        with Session(second_engine) as session:
            second = session.exec(select(ImportBatch)).one().file_sha256
        assert first != second

    def test_undo_empties_the_whole_ledger(self, export: Path, resolutions: Path) -> None:
        """The M0 definition of done: one batch id reverses one import."""
        engine = _engine()
        result = _import(engine, export, resolutions)
        assert undo_batch(engine, result.batch_id) == TOTAL_ROWS
        with Session(engine) as session:
            assert session.exec(select(Transaction)).all() == []
            assert session.exec(select(ImportBatch)).all() == []


class TestIdempotency:
    def test_a_second_import_inserts_nothing(self, export: Path, resolutions: Path) -> None:
        engine = _engine()
        account_id = ensure_default_account(engine)
        import_degiro_export(engine, export, account_id, resolutions)
        second = import_degiro_export(engine, export, account_id, resolutions)
        assert second.rows_inserted == 0
        assert second.rows_skipped == TOTAL_ROWS
        with Session(engine) as session:
            assert len(session.exec(select(Transaction)).all()) == TOTAL_ROWS

"""Opt-in suite: M0's definition of done, run against the owner's real exports.

Import the whole export, get every row; run it again, get none; undo it, get an
empty ledger. The golden fixture proves the mechanism on thirteen invented rows;
this proves it on 897 real ones, where the quirks that broke earlier assumptions
actually live.

Never runs in CI: the exports are gitignored, so these tests skip themselves when
the directory is absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.db import create_engine_and_tables
from app.ingest.importer import ensure_default_account, import_degiro_export, undo_batch
from app.models.ledger import Transaction
from tests.integration import realdata_subject as subject

EXPORT = Path(__file__).parents[3] / "degiro-export"

pytestmark = [
    pytest.mark.realdata,
    pytest.mark.skipif(
        not ((EXPORT / "Transactions.csv").exists() and (EXPORT / "Account.csv").exists()),
        reason="real DeGiro export not present",
    ),
]

# Both keys are read from the export, never written down: they name the owner's
# instruments, and Sec 13 keeps those out of the repo.
ORION_KEY = subject.split_key() if subject.available() else ""
MERIDIAN_KEY = subject.product_change_key() if subject.available() else ""

RESOLUTIONS = f"""\
resolutions:
  - key: {ORION_KEY}
    treatment: corporate_action
    note: ORION 10-for-1 split
  - key: {MERIDIAN_KEY}
    treatment: corporate_action
    note: Meridian Mining product change
"""

#: Every row of Transactions.csv.
TRADE_ROWS = 112
#: Account.csv's 785 rows less the 466 trade duplicates and internal transfers.
#: The per-type breakdown this sums is asserted in test_realdata_account.py.
CASH_ROWS = 319
TOTAL_ROWS = TRADE_ROWS + CASH_ROWS


def _engine() -> Engine:
    return create_engine_and_tables("sqlite://")


@pytest.fixture
def resolutions(tmp_path: Path) -> Path:
    path = tmp_path / "corporate_actions.yaml"
    path.write_text(RESOLUTIONS, encoding="utf-8")
    return path


def test_imports_every_row_of_both_exports(resolutions: Path) -> None:
    engine = _engine()
    result = import_degiro_export(engine, EXPORT, ensure_default_account(engine), resolutions)
    assert result.rows_parsed == TOTAL_ROWS
    assert result.rows_inserted == TOTAL_ROWS


def test_a_second_import_inserts_nothing(resolutions: Path) -> None:
    """Idempotency on the real file, where 105 order ids cover 112 rows and two
    fills are byte-identical -- the case that breaks a naive dedupe key."""
    engine = _engine()
    account_id = ensure_default_account(engine)
    import_degiro_export(engine, EXPORT, account_id, resolutions)
    again = import_degiro_export(engine, EXPORT, account_id, resolutions)
    assert again.rows_inserted == 0
    assert again.rows_skipped == TOTAL_ROWS


def test_undo_empties_the_ledger(resolutions: Path) -> None:
    engine = _engine()
    result = import_degiro_export(engine, EXPORT, ensure_default_account(engine), resolutions)
    assert undo_batch(engine, result.batch_id) == TOTAL_ROWS
    with Session(engine) as session:
        assert session.exec(select(Transaction)).all() == []


def test_the_two_corporate_actions_land_non_economic(resolutions: Path) -> None:
    """Eight rows, and only those eight: two events, each recorded twice -- as an
    offsetting share pair in Transactions.csv and as the labelled cash pair in
    Account.csv. A ninth would mean a genuine trade had been suppressed."""
    engine = _engine()
    import_degiro_export(engine, EXPORT, ensure_default_account(engine), resolutions)
    with Session(engine) as session:
        rows = session.exec(select(Transaction)).all()
    suppressed = [row for row in rows if not row.is_economic]
    assert len(suppressed) == 8
    assert {row.isin for row in suppressed} == subject.suppressed_isins()


def test_an_unresolved_export_imports_nothing(tmp_path: Path) -> None:
    """The gate, on the real file: without the answers, the ledger stays empty."""
    from app.ingest.importer import QuarantineError

    engine = _engine()
    with pytest.raises(QuarantineError):
        import_degiro_export(
            engine, EXPORT, ensure_default_account(engine), tmp_path / "absent.yaml"
        )
    with Session(engine) as session:
        assert session.exec(select(Transaction)).all() == []


def test_every_euro_amount_in_the_ledger_is_actually_euros(resolutions: Path) -> None:
    """The convention `normalise_account_rows` encodes, checked against 316 real
    cash rows in four currencies: a non-zero `net_base` always means euros moved."""
    engine = _engine()
    import_degiro_export(engine, EXPORT, ensure_default_account(engine), resolutions)
    with Session(engine) as session:
        rows = session.exec(select(Transaction)).all()
    foreign_cash = [
        row
        for row in rows
        if row.quantity is None and row.currency_local not in (None, "EUR") and row.net_base != 0
    ]
    assert foreign_cash == []

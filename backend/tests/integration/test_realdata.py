"""Opt-in suite against the owner's real exports. Never runs in CI: the files are
gitignored, so these tests skip themselves when the directory is absent.
PYTEST_DONT_REWRITE -- pytest's assertion rewriting prints both operands of a
failing assert, and the operands here are derived from the gitignored export:
identifiers, balances, dates, and model reprs that carry all three. That output
reaches a terminal, and from there agent transcripts, pasted reports and issue
comments. The marker turns the rewriting off, so a failure reports only what
its own message says.
"""

from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.db import create_engine_and_tables
from app.ingest.importer import ensure_default_account, import_transactions_file
from app.models.ledger import Transaction

REAL = Path(__file__).parents[3] / "degiro-export" / "Transactions.csv"

pytestmark = [
    pytest.mark.realdata,
    pytest.mark.skipif(not REAL.exists(), reason="real DeGiro export not present"),
]


def test_real_file_imports_every_row_and_is_idempotent() -> None:
    engine = create_engine_and_tables("sqlite://")
    account_id = ensure_default_account(engine)

    first = import_transactions_file(engine, REAL, account_id)
    assert first.rows_inserted == first.rows_parsed

    second = import_transactions_file(engine, REAL, account_id)
    assert second.rows_inserted == 0

    with Session(engine) as s:
        assert len(s.exec(select(Transaction)).all()) == first.rows_parsed

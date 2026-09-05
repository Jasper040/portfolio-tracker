"""Import orchestration.

Every import writes an `import_batch` and is fully reversible. Idempotency comes
from the unique `(source, source_ref)` constraint plus a pre-read of existing refs,
so re-importing a file inserts nothing rather than raising.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import Engine
from sqlmodel import Session, select

from app.ingest.degiro.transactions_csv import PARSER_VERSION, SOURCE, parse_transactions_csv
from app.models.ledger import Account, ImportBatch, Transaction

DEFAULT_ACCOUNT_NAME = "DeGiro Main"


@dataclass(frozen=True, slots=True)
class ImportResult:
    batch_id: UUID
    rows_parsed: int
    rows_inserted: int
    rows_skipped: int


def ensure_default_account(engine: Engine) -> UUID:
    with Session(engine) as session:
        existing = session.exec(select(Account).where(Account.broker == SOURCE)).first()
        if existing is not None:
            return existing.id
        account = Account(
            id=uuid4(), broker=SOURCE, name=DEFAULT_ACCOUNT_NAME, base_currency="EUR"
        )
        session.add(account)
        session.commit()
        return account.id


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def import_transactions_file(engine: Engine, path: Path, account_id: UUID) -> ImportResult:
    """Parse `path` and insert every row not already present.

    A `MalformedRow` from the parser is allowed to propagate: a broken export must
    abort the whole import rather than land partially, so it is not caught here.
    """
    rows = parse_transactions_csv(path)
    batch_id = uuid4()

    with Session(engine) as session:
        known = set(
            session.exec(select(Transaction.source_ref).where(Transaction.source == SOURCE)).all()
        )
        fresh = [row for row in rows if row.source_ref not in known]

        # The batch row must exist before any transaction references it as a foreign
        # key: SQLite now enforces `PRAGMA foreign_keys=ON` (Task 4), so this insert
        # order is not cosmetic.
        session.add(
            ImportBatch(
                id=batch_id,
                source=SOURCE,
                filename=path.name,
                file_sha256=_sha256(path),
                parser_version=PARSER_VERSION,
                imported_at=datetime.now(timezone.utc),
                row_count=len(rows),
                inserted_count=len(fresh),
            )
        )
        for row in fresh:
            session.add(
                Transaction(
                    id=uuid4(),
                    account_id=account_id,
                    import_batch_id=batch_id,
                    source=row.source,
                    source_ref=row.source_ref,
                    txn_type=row.txn_type,
                    trade_date=row.trade_date,
                    settle_date=row.settle_date,
                    isin=row.isin,
                    product_name=row.product_name,
                    quantity=row.quantity,
                    price_local=row.price_local,
                    currency_local=row.currency_local,
                    fx_rate=row.fx_rate,
                    fee_base=row.fee_base,
                    tax_base=row.tax_base,
                    gross_local=row.gross_local,
                    net_base=row.net_base,
                    order_ref=row.order_ref,
                    is_economic=row.is_economic,
                    closure_reason=row.closure_reason,
                    raw_json=json.dumps(row.raw, ensure_ascii=False),
                )
            )
        session.commit()

    return ImportResult(
        batch_id=batch_id,
        rows_parsed=len(rows),
        rows_inserted=len(fresh),
        rows_skipped=len(rows) - len(fresh),
    )


def undo_batch(engine: Engine, batch_id: UUID) -> int:
    """Remove one import batch and every transaction it created.

    Transactions are deleted before the batch they reference: with SQLite's foreign
    keys now enforced (Task 4), deleting the batch first would raise instead of
    silently leaving orphans, so this ordering is load-bearing.
    """
    with Session(engine) as session:
        doomed = session.exec(
            select(Transaction).where(Transaction.import_batch_id == batch_id)
        ).all()
        for txn in doomed:
            session.delete(txn)
        session.flush()
        batch = session.get(ImportBatch, batch_id)
        if batch is not None:
            session.delete(batch)
        session.commit()
        return len(doomed)

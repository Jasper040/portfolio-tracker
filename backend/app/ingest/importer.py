"""Import orchestration.

Every import writes an `import_batch` and is fully reversible. Idempotency comes
from the unique `(source, source_ref)` constraint plus a pre-read of existing refs,
so re-importing a file inserts nothing rather than raising.

`import_degiro_export` is the entry point that enforces design doc Sec 6.3: an
export with an unresolved corporate action does not import at all. The refusal is
total rather than partial-with-a-warning because a split booked as a trade does not
announce itself -- it just makes every realised figure for that instrument wrong,
and a ledger that is 99% right looks exactly like one that is right.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import Engine
from sqlmodel import Session, select

from app.ingest.corporate_actions import (
    CorporateAction,
    Resolution,
    detect,
    load_resolutions,
    pending,
    suppressed_refs,
)
from app.ingest.degiro.account_csv import parse_account_csv
from app.ingest.degiro.transactions_csv import PARSER_VERSION, SOURCE, parse_transactions_csv
from app.models.ledger import Account, CorporateActionReview, ImportBatch, Transaction

DEFAULT_ACCOUNT_NAME = "DeGiro Main"

TRANSACTIONS_FILENAME = "Transactions.csv"
ACCOUNT_FILENAME = "Account.csv"


class QuarantineError(RuntimeError):
    """Import refused: Sec 6.3 corporate actions are still waiting on a human."""

    def __init__(self, unresolved: Sequence[CorporateAction]) -> None:
        self.pending: tuple[CorporateAction, ...] = tuple(unresolved)
        super().__init__(
            f"{len(self.pending)} unresolved corporate action(s); "
            "answer them in the resolutions file before importing"
        )


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


def import_transactions_file(
    engine: Engine,
    path: Path,
    account_id: UUID,
    suppressed: Mapping[str, str] | None = None,
) -> ImportResult:
    """Parse `path` and insert every row not already present.

    A `MalformedRow` from the parser is allowed to propagate: a broken export must
    abort the whole import rather than land partially, so it is not caught here.

    `suppressed` maps a `source_ref` to the reason it is not an economic event --
    a resolved corporate action. Those rows are still inserted, because the ledger
    records what the export said, but they are flagged so lot matching skips them.
    This is the mechanical half of the import; the Sec 6.3 gate that decides what
    belongs in `suppressed` lives in `import_degiro_export`.
    """
    reasons = suppressed or {}
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
                    trade_time=row.trade_time,
                    settle_date=row.settle_date,
                    isin=row.isin,
                    product_name=row.product_name,
                    quantity=row.quantity,
                    price_local=row.price_local,
                    currency_local=row.currency_local,
                    fx_rate=row.fx_rate,
                    fee_base=row.fee_base,
                    tax_base=row.tax_base,
                    autofx_fee_base=row.autofx_fee_base,
                    gross_local=row.gross_local,
                    net_base=row.net_base,
                    order_ref=row.order_ref,
                    is_economic=row.is_economic and row.source_ref not in reasons,
                    # Deliberately untouched by suppression. Its vocabulary
                    # (DECISION, TRANSFER, PRODUCT_CHANGE, DELISTING) describes why
                    # a LOT closed, and a suppressed row never closes one. The
                    # reason goes in `note`, where it can be read without a join.
                    closure_reason=row.closure_reason,
                    raw_json=json.dumps(row.raw, ensure_ascii=False),
                    note=reasons.get(row.source_ref),
                )
            )
        session.commit()

    return ImportResult(
        batch_id=batch_id,
        rows_parsed=len(rows),
        rows_inserted=len(fresh),
        rows_skipped=len(rows) - len(fresh),
    )


def _suppression_notes(
    candidates: Sequence[CorporateAction], resolutions: Mapping[str, Resolution]
) -> dict[str, str]:
    """Which rows to flag, and the sentence explaining why, keyed by `source_ref`."""
    refs = suppressed_refs(candidates, resolutions)
    return {
        ref: f"corporate action {candidate.key} ({candidate.kind})"
        for candidate in candidates
        for ref in candidate.source_refs
        if ref in refs
    }


def rebuild_review_queue(
    engine: Engine,
    candidates: Sequence[CorporateAction],
    resolutions: Mapping[str, Resolution],
) -> None:
    """Replace the review queue with the current candidates.

    Replace, not append: the queue is a projection of the export and the
    resolutions file, so rebuilding it is the only way it can never disagree with
    them. An accumulating log would surface events already answered, and a review
    queue nobody trusts is a review queue nobody reads.
    """
    detected_at = datetime.now(timezone.utc)
    with Session(engine) as session:
        for stale in session.exec(select(CorporateActionReview)).all():
            session.delete(stale)
        session.flush()
        for candidate in candidates:
            resolution = resolutions.get(candidate.key)
            session.add(
                CorporateActionReview(
                    id=uuid4(),
                    key=candidate.key,
                    trade_date=candidate.trade_date,
                    isin=candidate.isin,
                    local_amount=candidate.local_amount,
                    kind=candidate.kind,
                    label=candidate.label,
                    source_refs=json.dumps(list(candidate.source_refs)),
                    detected_at=detected_at,
                    resolved=resolution is not None,
                    note=resolution.note if resolution is not None else None,
                )
            )
        session.commit()


def import_degiro_export(
    engine: Engine, export_dir: Path, account_id: UUID, resolutions_path: Path
) -> ImportResult:
    """Import a DeGiro export directory, refusing while a corporate action is open.

    Both files are read before anything is written. `Account.csv` is not imported
    here -- it is authoritative for everything that is not a trade (Sec 6.2), but
    what it contributes to *this* step is the corporate-action label that
    `Transactions.csv` does not carry.

    The file is parsed twice: once here to detect, once inside
    `import_transactions_file`, which owns the file hash and parser version it
    records on the batch. Source refs are deterministic, so the two parses agree by
    construction, and 112 rows is not worth coupling the two steps to avoid.
    """
    transactions_path = export_dir / TRANSACTIONS_FILENAME
    account_path = export_dir / ACCOUNT_FILENAME
    for required in (transactions_path, account_path):
        if not required.exists():
            raise FileNotFoundError(f"{required} is missing; both DeGiro exports are required")

    candidates = detect(parse_transactions_csv(transactions_path), parse_account_csv(account_path))
    resolutions = load_resolutions(resolutions_path)

    # Written before the refusal, not after: the operator answers the questions in
    # a different terminal than the one that raised, so the queue has to outlive
    # the process that found them.
    rebuild_review_queue(engine, candidates, resolutions)

    unresolved = pending(candidates, resolutions)
    if unresolved:
        raise QuarantineError(unresolved)

    return import_transactions_file(
        engine,
        transactions_path,
        account_id,
        suppressed=_suppression_notes(candidates, resolutions),
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

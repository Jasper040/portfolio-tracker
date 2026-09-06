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

from app.ingest.base import NormalisedRow
from app.ingest.corporate_actions import (
    CorporateAction,
    Resolution,
    detect,
    load_resolutions,
    pending,
    suppressed_refs,
)
from app.ingest.degiro.account_csv import PARSER_VERSION as ACCOUNT_PARSER_VERSION
from app.ingest.degiro.account_csv import normalise_account_rows, parse_account_csv
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


def _sha256(*paths: Path) -> str:
    """Hash the whole set of files an import read.

    Hashing the concatenated bytes rather than one file: a batch covers both
    exports, and re-running against a changed `Account.csv` is a different import
    even when the trade file is byte-identical.
    """
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes())
    return digest.hexdigest()


def import_transactions_file(
    engine: Engine,
    path: Path,
    account_id: UUID,
    suppressed: Mapping[str, str] | None = None,
) -> ImportResult:
    """Parse `path` and insert every trade row not already present.

    A `MalformedRow` from the parser is allowed to propagate: a broken export must
    abort the whole import rather than land partially, so it is not caught here.

    This is the trade file alone, without the Sec 6.3 gate. `import_degiro_export`
    is the operator-facing entry point.
    """
    return insert_rows(
        engine,
        account_id,
        parse_transactions_csv(path),
        filename=path.name,
        file_sha256=_sha256(path),
        parser_version=PARSER_VERSION,
        suppressed=suppressed,
    )


def insert_rows(
    engine: Engine,
    account_id: UUID,
    rows: Sequence[NormalisedRow],
    *,
    filename: str,
    file_sha256: str,
    parser_version: str,
    suppressed: Mapping[str, str] | None = None,
) -> ImportResult:
    """Write one batch and the rows it inserts. The mechanical half of an import.

    `suppressed` maps a `source_ref` to the reason it is not an economic event --
    a resolved corporate action. Those rows are still inserted, because the ledger
    records what the export said, but they are flagged so lot matching skips them.
    """
    reasons = suppressed or {}
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
                filename=filename,
                file_sha256=file_sha256,
                parser_version=parser_version,
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
                    value_base=row.value_base,
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
    candidates: Sequence[CorporateAction],
    resolutions: Mapping[str, Resolution],
    cash_rows: Sequence[NormalisedRow] = (),
) -> dict[str, str]:
    """Which rows to flag, and the sentence explaining why, keyed by `source_ref`.

    DeGiro records a corporate action twice: as an offsetting share pair in
    `Transactions.csv` and as the labelled cash pair in `Account.csv` that names
    it. Both are legs of one event, so both are flagged. Flagging only the trade
    file would leave `is_economic` meaning "not a trade" on one file and "not an
    event" on the other, and the ledger would carry the same amount twice, once
    marked and once not.

    They are flagged, never dropped: the label rows are where M1 reads the ratio,
    stated in the broker's own words (`SPLIT AANPASSING: 10 ORION @ 90,666` next
    to `1 ORION @ 910,40` is the 10-for-1).
    """
    refs = suppressed_refs(candidates, resolutions)
    resolved = [candidate for candidate in candidates if set(candidate.source_refs) & refs]

    notes = {
        ref: f"corporate action {candidate.key} ({candidate.kind})"
        for candidate in resolved
        for ref in candidate.source_refs
    }

    # The same (date, isin, abs(local amount)) key detection joined on, applied to
    # the other file. Restricted to rows the taxonomy already called a corporate
    # action, so an ordinary trade duplicate of the same size cannot be caught.
    events = {
        (candidate.trade_date, candidate.isin, candidate.local_amount): candidate
        for candidate in resolved
    }
    for row in cash_rows:
        if row.txn_type != "CORPORATE_ACTION" or row.isin is None or row.gross_local is None:
            continue
        candidate = events.get((row.trade_date, row.isin, abs(row.gross_local)))
        if candidate is not None:
            notes[row.source_ref] = f"corporate action {candidate.key} ({candidate.kind})"
    return notes


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

    Both files land in one batch. `Transactions.csv` is authoritative for trades
    and `Account.csv` for everything else (Sec 6.2), so a ledger built from only
    the first is a trade blotter with no dividends, deposits or fees -- and an
    import is one operation, which Sec 3's undo has to reverse with one id.

    Everything is read and checked before anything is written: an export with an
    unanswered corporate action leaves no batch and no rows at all.
    """
    transactions_path = export_dir / TRANSACTIONS_FILENAME
    account_path = export_dir / ACCOUNT_FILENAME
    for required in (transactions_path, account_path):
        if not required.exists():
            raise FileNotFoundError(f"{required} is missing; both DeGiro exports are required")

    trades = parse_transactions_csv(transactions_path)
    account = parse_account_csv(account_path)

    candidates = detect(trades, account)
    resolutions = load_resolutions(resolutions_path)

    # Written before the refusal, not after: the operator answers the questions in
    # a different terminal than the one that raised, so the queue has to outlive
    # the process that found them.
    rebuild_review_queue(engine, candidates, resolutions)

    unresolved = pending(candidates, resolutions)
    if unresolved:
        raise QuarantineError(unresolved)

    cash_rows = normalise_account_rows(account)
    return insert_rows(
        engine,
        account_id,
        [*trades, *cash_rows],
        filename=f"{TRANSACTIONS_FILENAME}+{ACCOUNT_FILENAME}",
        file_sha256=_sha256(transactions_path, account_path),
        parser_version=f"{PARSER_VERSION}+{ACCOUNT_PARSER_VERSION}",
        suppressed=_suppression_notes(candidates, resolutions, cash_rows),
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

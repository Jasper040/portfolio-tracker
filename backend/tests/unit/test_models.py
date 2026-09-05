from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlmodel import Session, select

from app.db import create_engine_and_tables, get_session
from app.models.ledger import Account, ImportBatch, Transaction


def test_get_session_returns_working_session() -> None:
    """get_session is a declared interface (see brief); later tasks import it."""
    engine = create_engine_and_tables("sqlite://")
    account_id = uuid4()
    with get_session(engine) as s:
        assert isinstance(s, Session)
        s.add(Account(id=account_id, broker="degiro", name="Main", base_currency="EUR"))
        s.commit()

    with get_session(engine) as s:
        account = s.get(Account, account_id)
        assert account is not None
        assert account.broker == "degiro"


def test_decimal_round_trips_exactly() -> None:
    """0.1 + 0.2 style drift must be impossible; SQLite must not see a float."""
    engine = create_engine_and_tables("sqlite://")
    account_id = uuid4()
    batch_id = uuid4()
    with Session(engine) as s:
        s.add(Account(id=account_id, broker="degiro", name="Main", base_currency="EUR"))
        s.add(
            ImportBatch(
                id=batch_id,
                source="degiro",
                filename="Transactions.csv",
                file_sha256="abc",
                parser_version="1",
                imported_at=datetime.now(timezone.utc),
                row_count=1,
                inserted_count=1,
            )
        )
        s.add(
            Transaction(
                id=uuid4(),
                account_id=account_id,
                import_batch_id=batch_id,
                source="degiro",
                source_ref="ref-1",
                txn_type="BUY",
                trade_date=date(2026, 7, 13),
                isin="US0000000903",
                quantity=Decimal("2"),
                price_local=Decimal("502.1500"),
                currency_local="USD",
                fx_rate=Decimal("1.2150"),
                fee_base=Decimal("-2.00"),
                tax_base=Decimal("0.00"),
                gross_local=Decimal("-1004.25"),
                net_base=Decimal("-883.10"),
                order_ref="6e89ea79",
                is_economic=True,
                closure_reason="DECISION",
                raw_json='{"a": 1}',
            )
        )
        s.commit()

    with Session(engine) as s:
        txn = s.exec(select(Transaction)).one()
        assert txn.price_local == Decimal("502.1500")
        assert txn.net_base == Decimal("-883.10")
        assert isinstance(txn.price_local, Decimal)


def test_source_ref_is_unique() -> None:
    """Idempotency is enforced by the database, not only by application logic."""
    engine = create_engine_and_tables("sqlite://")
    from sqlalchemy.exc import IntegrityError

    account_id = uuid4()
    batch_id = uuid4()

    def make(ref: str) -> Transaction:
        return Transaction(
            id=uuid4(),
            account_id=account_id,
            import_batch_id=batch_id,
            source="degiro",
            source_ref=ref,
            txn_type="BUY",
            trade_date=date(2026, 1, 1),
            fee_base=Decimal("0"),
            tax_base=Decimal("0"),
            net_base=Decimal("-1"),
            is_economic=True,
            closure_reason="DECISION",
            raw_json="{}",
        )

    with Session(engine) as s:
        s.add(Account(id=account_id, broker="degiro", name="M", base_currency="EUR"))
        s.add(
            ImportBatch(
                id=batch_id,
                source="degiro",
                filename="f",
                file_sha256="h",
                parser_version="1",
                imported_at=datetime.now(timezone.utc),
                row_count=0,
                inserted_count=0,
            )
        )
        s.add(make("dupe"))
        s.commit()
        s.add(make("dupe"))
        try:
            s.commit()
        except IntegrityError:
            return
    raise AssertionError("duplicate source_ref was accepted")


def test_orphan_foreign_keys_are_rejected() -> None:
    """SQLite ignores declared FKs unless the pragma is set. Without it the local
    suite accepts rows Postgres rejects, so a referential bug — a bad undo ordering,
    a stale account_id — would only ever appear in production."""
    from sqlalchemy.exc import IntegrityError

    engine = create_engine_and_tables("sqlite://")
    with Session(engine) as s:
        s.add(
            Transaction(
                id=uuid4(),
                account_id=uuid4(),  # no such account
                import_batch_id=uuid4(),  # no such batch
                source="degiro",
                source_ref="orphan",
                txn_type="BUY",
                trade_date=date(2026, 1, 1),
                fee_base=Decimal("0"),
                tax_base=Decimal("0"),
                net_base=Decimal("-1"),
                is_economic=True,
                closure_reason="DECISION",
                raw_json="{}",
            )
        )
        with pytest.raises(IntegrityError):
            s.commit()

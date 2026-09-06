"""The ledger. Append-only and immutable: see the design doc, section 3."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Column, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.types import DecimalString


class Account(SQLModel, table=True):
    __tablename__ = "account"

    id: UUID = Field(primary_key=True)
    broker: str
    name: str
    base_currency: str


class Instrument(SQLModel, table=True):
    __tablename__ = "instrument"

    id: UUID = Field(primary_key=True)
    isin: str = Field(unique=True, index=True)
    name: str
    currency: str
    instrument_type: str = "equity"


class ImportBatch(SQLModel, table=True):
    __tablename__ = "import_batch"

    id: UUID = Field(primary_key=True)
    source: str
    filename: str
    file_sha256: str
    parser_version: str
    imported_at: datetime
    row_count: int
    inserted_count: int


class CorporateActionReview(SQLModel, table=True):
    """The quarantine queue (design doc Sec 6.3). Deliberately NOT part of the ledger.

    A projection of `(export, config/corporate_actions.yaml)`, rebuilt on every
    import rather than appended to. The answers live in the YAML file, so a row
    here holds no state of its own -- and a queue that accumulated would keep
    asking questions the operator has already answered, which is the fastest way to
    train someone to ignore it.

    That also means the append-only rule does not apply: nothing here is a
    financial fact, it is a to-do list derived from facts held elsewhere.
    """

    __tablename__ = "corporate_action_review"
    __table_args__ = (UniqueConstraint("key", name="uq_corporate_action_review_key"),)

    id: UUID = Field(primary_key=True)
    #: `isin:date:amount` -- the same key the resolutions file is written against.
    key: str = Field(index=True)
    trade_date: date = Field(index=True)
    isin: str = Field(index=True)
    local_amount: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    #: SPLIT, PRODUCT_CHANGE, or UNLABELLED when Account.csv did not name it.
    kind: str
    label: str
    #: JSON list of the `transaction.source_ref`s this event covers.
    source_refs: str
    detected_at: datetime
    resolved: bool = False
    note: str | None = None


class Transaction(SQLModel, table=True):
    """One economic event. Never updated in place; only inserted or batch-deleted."""

    __tablename__ = "transaction"
    __table_args__ = (UniqueConstraint("source", "source_ref", name="uq_txn_source_ref"),)

    id: UUID = Field(primary_key=True)
    account_id: UUID = Field(foreign_key="account.id", index=True)
    import_batch_id: UUID = Field(foreign_key="import_batch.id", index=True)

    source: str
    source_ref: str = Field(index=True)

    txn_type: str = Field(index=True)
    trade_date: date = Field(index=True)
    # The raw "HH:MM" cell, deliberately a string rather than a datetime: DeGiro
    # never states a timezone, and inventing one would fabricate a fact in a
    # ledger built on broker truth. Combined with trade_date and isin, this is
    # what M1 groups fill rows into economic orders on (design doc Sec 6.4).
    trade_time: str | None = None
    settle_date: date | None = None

    isin: str | None = Field(default=None, index=True)
    product_name: str | None = None

    quantity: Decimal | None = Field(default=None, sa_column=Column(DecimalString()))
    price_local: Decimal | None = Field(default=None, sa_column=Column(DecimalString()))
    currency_local: str | None = None
    fx_rate: Decimal | None = Field(default=None, sa_column=Column(DecimalString()))

    fee_base: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    tax_base: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    # A real per-row cost DeGiro charges on foreign-currency fills. `net_base` (broker
    # truth) already reflects it, so cash is correct without this column; it is stored
    # so M1's per-lot cost attribution does not have to re-parse raw_json to find it.
    autofx_fee_base: Decimal | None = Field(default=None, sa_column=Column(DecimalString()))
    gross_local: Decimal | None = Field(default=None, sa_column=Column(DecimalString()))
    net_base: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))

    order_ref: str | None = Field(default=None, index=True)
    is_economic: bool = True
    closure_reason: str = "DECISION"

    raw_json: str
    note: str | None = None

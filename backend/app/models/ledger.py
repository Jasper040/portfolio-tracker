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
    settle_date: date | None = None

    isin: str | None = Field(default=None, index=True)
    product_name: str | None = None

    quantity: Decimal | None = Field(default=None, sa_column=Column(DecimalString()))
    price_local: Decimal | None = Field(default=None, sa_column=Column(DecimalString()))
    currency_local: str | None = None
    fx_rate: Decimal | None = Field(default=None, sa_column=Column(DecimalString()))

    fee_base: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    tax_base: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    gross_local: Decimal | None = Field(default=None, sa_column=Column(DecimalString()))
    net_base: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))

    order_ref: str | None = Field(default=None, index=True)
    is_economic: bool = True
    closure_reason: str = "DECISION"

    raw_json: str
    note: str | None = None

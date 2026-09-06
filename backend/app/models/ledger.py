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


class Lot(SQLModel, table=True):
    """One open purchase lot under one lot method. Derived, not ledger.

    Rewritten wholesale by every `rebuild()`, which is why it carries `method`: FIFO,
    LIFO and HIFO produce different lots from identical rows, and a table that could
    not say which one it holds would be a number without a provenance -- exactly
    what Sec 9.2 exists to prevent.

    Charges are stored in three columns rather than one total, and separately from
    `cost_basis`, because Sec 6.4 says a lot must be able to report what the shares
    cost and what the broker charged as two different answers.
    """

    __tablename__ = "lot"
    __table_args__ = (UniqueConstraint("method", "source_ref", name="uq_lot_method_ref"),)

    id: UUID = Field(primary_key=True)
    method: str = Field(index=True)
    isin: str = Field(index=True)
    #: The opening transaction's `source_ref`. Ties a derived lot to the fact it came
    #: from without a foreign key into a table that gets rewritten.
    source_ref: str = Field(index=True)

    opened_on: date = Field(index=True)
    #: Post-split. `price` is likewise split-adjusted, so `quantity * price`
    #: still equals `cost_basis`.
    quantity: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    price: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    cost_basis: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))

    commission: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    autofx: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    tax: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))


class LotClosure(SQLModel, table=True):
    """One matched (lot, sale) pair under one lot method. Derived, not ledger."""

    __tablename__ = "lot_closure"

    id: UUID = Field(primary_key=True)
    method: str = Field(index=True)
    isin: str = Field(index=True)
    lot_source_ref: str = Field(index=True)
    sale_source_ref: str = Field(index=True)

    opened_on: date
    closed_on: date = Field(index=True)
    quantity: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    open_price: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    close_price: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))

    #: What the stock did, before charges. Stored rather than recomputed at read
    #: time so the API cannot disagree with the rebuild that wrote it.
    gross_pnl: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    commission: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    autofx: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    tax: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    pnl: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))

    holding_days: int
    #: Sec 7.1 requires both on every closure. Nullable because both are genuinely
    #: undefined in cases the matcher meets: a zero basis has no return, and a
    #: same-day round trip or a total loss has no annualised one. NULL is the honest
    #: answer there -- a zero would read as "no gain", which is a different claim.
    return_pct: Decimal | None = Field(default=None, sa_column=Column(DecimalString()))
    annualised_return: Decimal | None = Field(
        default=None, sa_column=Column(DecimalString())
    )


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
    # The trade value in EUR before fees (DeGiro's "Value EUR"), stored rather than
    # derived as `net_base - fee_base - autofx_fee_base`: Sec 3.5 records that the
    # broker's own arithmetic disagrees with itself by EUR 0.01 on 14 of 112 rows,
    # and Sec 5.4 forbids recomputing a broker-stated figure. `domain/orders.py`
    # divides this by quantity to get the base-currency price the lot matcher needs.
    value_base: Decimal | None = Field(default=None, sa_column=Column(DecimalString()))
    net_base: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))

    order_ref: str | None = Field(default=None, index=True)
    is_economic: bool = True
    closure_reason: str = "DECISION"

    raw_json: str
    note: str | None = None


class PositionDaily(SQLModel, table=True):
    """Shares held in one instrument at the end of one day. Derived, not ledger.

    **No `method` column, on purpose.** FIFO, LIFO and HIFO disagree about which
    lot a sale consumed and therefore about realised P&L, but they cannot
    disagree about how many shares are left -- M1 asserts exactly that in
    `test_every_method_holds_the_same_shares`. A method column here would store
    three copies of one answer, and the day two of them differed there would be
    no way to say which was right.

    Rows exist only for days the position was non-zero. An absent row means "not
    held", which is a different statement from "held zero" and reads correctly in
    the valuation join without a special case.

    Quantities are post-split, because the fills they come from have already been
    through `apply_splits`.
    """

    __tablename__ = "position_daily"
    __table_args__ = (
        UniqueConstraint("position_date", "isin", name="uq_position_daily_date_isin"),
    )

    id: UUID = Field(primary_key=True)
    position_date: date = Field(index=True)
    isin: str = Field(index=True)
    quantity: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))


class CashDaily(SQLModel, table=True):
    """The cash balance at the end of one day. Derived, not ledger.

    A running sum of `net_base`, which is broker truth (Sec 5.4) -- so this is
    pure ledger arithmetic and belongs on the derived side of the line, even
    though it ends up in the same chart as a fetched price.

    It exists because M2-4 makes portfolio value **net**: holdings at market plus
    cash, so the account's debit balance reduces the total rather than being
    quietly left out. Cash is reported as its own component so a reader can see
    which half moved.
    """

    __tablename__ = "cash_daily"
    __table_args__ = (UniqueConstraint("cash_date", name="uq_cash_daily_date"),)

    id: UUID = Field(primary_key=True)
    cash_date: date = Field(index=True)
    balance_base: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))

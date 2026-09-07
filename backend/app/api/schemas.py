"""API response models.

Money crosses the wire as a string. JSON numbers are IEEE doubles, so serialising a
Decimal as a number reintroduces exactly the drift the storage layer prevents.

Every response envelope inherits `Provenance`, which is what design doc Sec 9.2 means by
"API response schemas cannot be constructed without method and coverage fields": neither
field has a default, so Pydantic rejects an envelope that omits them. A convention would
decay the first time someone was in a hurry; a required field cannot.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, field_serializer

from app.models.ledger import Lot, LotClosure, Transaction
from app.models.types import Coverage

#: Which lot-matching method produced the realised figures in a response.
#: `None` is a real answer, not a missing one: it means no matching was applied,
#: which is the honest description of a raw ledger listing. Filling it with the
#: configured default would claim a computation that never ran.
LotMethod = Literal["FIFO", "LIFO", "HIFO"]

#: How much of the requested data the response could actually account for
#: (design doc Sec 8.1). Anything short of "full" means the UI must render "no data"
#: rather than a zero, and aggregates must say how much they cover. Defined in
#: `app.models.types` (imported above), not here, so `analytics/` can share the
#: same Literal without importing the API layer -- see that module for why.


class Provenance(BaseModel):
    """What produced these numbers. Inherited by every response envelope.

    Deliberately without defaults. `method=None` still has to be written out at the
    call site, which forces whoever adds an endpoint to decide what it means there.
    """

    method: LotMethod | None
    coverage: Coverage


class TransactionOut(BaseModel):
    id: str
    trade_date: date
    txn_type: str
    isin: str | None
    product_name: str | None
    quantity: Decimal | None
    price_local: Decimal | None
    currency_local: str | None
    fx_rate: Decimal | None
    fee_base: Decimal
    tax_base: Decimal
    net_base: Decimal
    order_ref: str | None
    is_economic: bool
    closure_reason: str
    raw: dict[str, str]

    @field_serializer(
        "quantity", "price_local", "fx_rate", "fee_base", "tax_base", "net_base"
    )
    def _decimal_as_string(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)

    @classmethod
    def from_model(cls, txn: Transaction) -> "TransactionOut":
        return cls(
            id=str(txn.id),
            trade_date=txn.trade_date,
            txn_type=txn.txn_type,
            isin=txn.isin,
            product_name=txn.product_name,
            quantity=txn.quantity,
            price_local=txn.price_local,
            currency_local=txn.currency_local,
            fx_rate=txn.fx_rate,
            fee_base=txn.fee_base,
            tax_base=txn.tax_base,
            net_base=txn.net_base,
            order_ref=txn.order_ref,
            is_economic=txn.is_economic,
            closure_reason=txn.closure_reason,
            raw=json.loads(txn.raw_json),
        )


class TransactionPage(Provenance):
    items: list[TransactionOut]
    total: int
    limit: int
    offset: int


class LotOut(BaseModel):
    id: str
    method: str
    isin: str
    source_ref: str
    opened_on: date
    quantity: Decimal
    price: Decimal
    cost_basis: Decimal
    commission: Decimal
    autofx: Decimal
    tax: Decimal

    @field_serializer("quantity", "price", "cost_basis", "commission", "autofx", "tax")
    def _decimal_as_string(self, value: Decimal) -> str:
        return str(value)

    @classmethod
    def from_model(cls, lot: "Lot") -> "LotOut":
        return cls(
            id=str(lot.id),
            method=lot.method,
            isin=lot.isin,
            source_ref=lot.source_ref,
            opened_on=lot.opened_on,
            quantity=lot.quantity,
            price=lot.price,
            cost_basis=lot.cost_basis,
            commission=lot.commission,
            autofx=lot.autofx,
            tax=lot.tax,
        )


class LotPage(Provenance):
    items: list[LotOut]
    total: int


class ClosureOut(BaseModel):
    id: str
    method: str
    isin: str
    #: Both halves of the pair. `LotClosure` stores the sale's `source_ref` and
    #: `domain/lots.py` argues for it -- "a closure is a pair, and carrying only the
    #: buy would leave half of it untraceable back to the ledger". Omitted here it
    #: was written by every rebuild and read by nothing.
    lot_source_ref: str
    sale_source_ref: str
    opened_on: date
    closed_on: date
    quantity: Decimal
    open_price: Decimal
    close_price: Decimal
    gross_pnl: Decimal
    commission: Decimal
    autofx: Decimal
    tax: Decimal
    pnl: Decimal
    holding_days: int
    return_pct: Decimal | None
    annualised_return: Decimal | None

    @field_serializer(
        "quantity", "open_price", "close_price", "gross_pnl",
        "commission", "autofx", "tax", "pnl",
    )
    def _decimal_as_string(self, value: Decimal) -> str:
        return str(value)

    @field_serializer("return_pct", "annualised_return")
    def _optional_decimal_as_string(self, value: Decimal | None) -> str | None:
        """`None` stays `None`, never becomes "0". A same-day round trip has no
        annualised return; reporting zero would claim it broke even."""
        return None if value is None else str(value)

    @classmethod
    def from_model(cls, closure: "LotClosure") -> "ClosureOut":
        return cls(
            id=str(closure.id),
            method=closure.method,
            isin=closure.isin,
            lot_source_ref=closure.lot_source_ref,
            sale_source_ref=closure.sale_source_ref,
            opened_on=closure.opened_on,
            closed_on=closure.closed_on,
            quantity=closure.quantity,
            open_price=closure.open_price,
            close_price=closure.close_price,
            gross_pnl=closure.gross_pnl,
            commission=closure.commission,
            autofx=closure.autofx,
            tax=closure.tax,
            pnl=closure.pnl,
            holding_days=closure.holding_days,
            return_pct=closure.return_pct,
            annualised_return=closure.annualised_return,
        )


class ClosurePage(Provenance):
    items: list[ClosureOut]
    total: int


class ValuationPointOut(BaseModel):
    """One day of the net portfolio value.

    `value_base` and `holdings_base` are `None` -- not zero -- when a held
    instrument could not be priced. Sec 8.1: a total that quietly dropped a
    position looks exactly like a total that included it.
    """

    date: date
    holdings_base: Decimal | None
    cash_base: Decimal
    value_base: Decimal | None
    coverage: Coverage
    #: The share of the day's holdings value that is fresh or hand-supplied.
    #: `None` exactly when `coverage` is "missing": there is no total, so there
    #: is no denominator to take a fraction of.
    covered_pct: Decimal | None

    @field_serializer("cash_base")
    def _decimal_as_string(self, value: Decimal) -> str:
        return str(value)

    @field_serializer("holdings_base", "value_base", "covered_pct")
    def _optional_decimal_as_string(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)


class ValuationSeriesOut(Provenance):
    """The daily series.

    `method` is `None` on this envelope and that is a real answer, not a missing
    one: no lot matching was applied, and M1 proved share counts are
    method-independent. Filling it with the configured default would claim a
    computation that never ran.
    """

    items: list[ValuationPointOut]
    start: date | None
    end: date | None
    #: What the caller asked for, echoed back. With `clamped`, this is how the UI
    #: can say "you asked for five years and the account is two years old"
    #: instead of silently drawing a shorter chart.
    requested_from: date | None
    clamped: bool
    base_currency: str


class PositionOut(BaseModel):
    isin: str
    product_name: str
    #: The currency the PRICE is quoted in, which is the one the coverage story
    #: is about. It is normally the trade currency too; where a provider quotes
    #: elsewhere the symbol would not have been accepted at all.
    currency: str
    quantity: Decimal
    cost_basis: Decimal
    charges_base: Decimal
    price: Decimal | None
    price_date: date | None
    #: Which provider supplied the price. "manual" is what a reader needs to see
    #: beside a figure somebody typed.
    source: str | None
    market_value_base: Decimal | None
    gross_unrealised_base: Decimal | None
    unrealised_base: Decimal | None
    unrealised_pct: Decimal | None
    coverage: Coverage

    @field_serializer("quantity", "cost_basis", "charges_base")
    def _decimal_as_string(self, value: Decimal) -> str:
        return str(value)

    @field_serializer(
        "price", "market_value_base", "gross_unrealised_base",
        "unrealised_base", "unrealised_pct",
    )
    def _optional_decimal_as_string(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)


class PositionsOut(Provenance):
    items: list[PositionOut]
    as_of: date | None
    total_cost_basis: Decimal
    #: `None` when ANY position is unpriceable (Sec 8.1 at the aggregate level).
    total_market_value_base: Decimal | None
    total_unrealised_base: Decimal | None
    base_currency: str

    @field_serializer("total_cost_basis")
    def _decimal_as_string(self, value: Decimal) -> str:
        return str(value)

    @field_serializer("total_market_value_base", "total_unrealised_base")
    def _optional_decimal_as_string(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)

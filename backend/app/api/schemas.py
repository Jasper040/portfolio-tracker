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

#: Which lot-matching method produced the realised figures in a response.
#: `None` is a real answer, not a missing one: it means no matching was applied,
#: which is the honest description of a raw ledger listing. Filling it with the
#: configured default would claim a computation that never ran.
LotMethod = Literal["FIFO", "LIFO", "HIFO"]

#: How much of the requested data the response could actually account for
#: (design doc Sec 8.1). Anything short of "full" means the UI must render "no data"
#: rather than a zero, and aggregates must say how much they cover.
Coverage = Literal["missing", "partial", "manual", "full"]


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
    lot_source_ref: str
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

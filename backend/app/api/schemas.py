"""API response models.

Money crosses the wire as a string. JSON numbers are IEEE doubles, so serialising a
Decimal as a number reintroduces exactly the drift the storage layer prevents.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, field_serializer

from app.models.ledger import Transaction


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


class TransactionPage(BaseModel):
    items: list[TransactionOut]
    total: int
    limit: int
    offset: int

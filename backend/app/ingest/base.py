"""The normalised shape every source produces before it reaches the ledger."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class NormalisedRow:
    source: str
    source_ref: str
    txn_type: str
    trade_date: date
    net_base: Decimal
    fee_base: Decimal
    tax_base: Decimal
    raw: dict[str, str]
    settle_date: date | None = None
    isin: str | None = None
    product_name: str | None = None
    quantity: Decimal | None = None
    price_local: Decimal | None = None
    currency_local: str | None = None
    fx_rate: Decimal | None = None
    gross_local: Decimal | None = None
    value_base: Decimal | None = None
    autofx_fee_base: Decimal = Decimal("0.00")
    order_ref: str | None = None
    is_economic: bool = True
    closure_reason: str = "DECISION"

    @property
    def gross_base_components_sum(self) -> Decimal:
        """What `net_base` would be if the broker's own arithmetic were exact.

        Exposed only so reconciliation can measure the gap. Never stored: the
        design fixes `net_base` as broker truth because that is the amount that
        actually moved through the cash account.
        """
        return (self.value_base or Decimal("0")) + self.autofx_fee_base + self.fee_base

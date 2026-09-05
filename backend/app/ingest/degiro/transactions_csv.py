"""Parser for DeGiro's Transactions.csv (trades only).

Authoritative for trades. The same trades also appear in Account.csv as
`Koop`/`Verkoop` rows; those are dropped there in favour of these, because this
file carries the execution venue, the FX rate and the fee split.
"""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from app.ingest.base import NormalisedRow
from app.ingest.degiro.dialect import (
    TRANSACTIONS_HEADER,
    TRANSACTIONS_RAW_FIELDS,
    TxnCol,
    assert_header,
    parse_decimal,
    parse_dutch_date,
    parse_optional_decimal,
)
from app.ingest.source_ref import RefInput, assign_source_refs

PARSER_VERSION = "degiro-transactions-1"
SOURCE = "degiro"


def parse_transactions_csv(path: Path) -> list[NormalisedRow]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        assert_header(header, TRANSACTIONS_HEADER, path.name)
        raw_rows = [row for row in reader if any(cell.strip() for cell in row)]

    ref_inputs = [
        RefInput(
            order_ref=row[TxnCol.ORDER_ID].strip(),
            trade_datetime=f"{row[TxnCol.DATE].strip()}T{row[TxnCol.TIME].strip()}",
            isin=row[TxnCol.ISIN].strip(),
            quantity=row[TxnCol.QUANTITY].strip(),
            price=row[TxnCol.PRICE].strip(),
        )
        for row in raw_rows
    ]
    refs = assign_source_refs(ref_inputs)

    return [_to_normalised(row, ref) for row, ref in zip(raw_rows, refs, strict=True)]


def _to_normalised(row: list[str], source_ref: str) -> NormalisedRow:
    quantity = parse_decimal(row[TxnCol.QUANTITY])
    order_ref = row[TxnCol.ORDER_ID].strip() or None

    # A blank fee cell means the commission was booked on a sibling fill of the same
    # order, so this row genuinely paid nothing. Order-level attribution happens in M1.
    fee = parse_optional_decimal(row[TxnCol.TXN_FEE]) or Decimal("0.00")
    autofx = parse_optional_decimal(row[TxnCol.AUTOFX_FEE]) or Decimal("0.00")

    return NormalisedRow(
        source=SOURCE,
        source_ref=source_ref,
        txn_type="BUY" if quantity > 0 else "SELL",
        trade_date=parse_dutch_date(row[TxnCol.DATE]),
        isin=row[TxnCol.ISIN].strip() or None,
        product_name=row[TxnCol.PRODUCT].strip() or None,
        quantity=quantity,
        price_local=parse_decimal(row[TxnCol.PRICE]),
        currency_local=row[TxnCol.LOCAL_CCY].strip() or None,
        fx_rate=parse_optional_decimal(row[TxnCol.FX_RATE]),
        gross_local=parse_optional_decimal(row[TxnCol.LOCAL_VALUE]),
        value_base=parse_optional_decimal(row[TxnCol.VALUE_EUR]),
        autofx_fee_base=autofx,
        fee_base=fee,
        tax_base=Decimal("0.00"),
        net_base=parse_decimal(row[TxnCol.TOTAL_EUR]),
        order_ref=order_ref,
        is_economic=True,
        closure_reason="DECISION",
        raw=dict(zip(TRANSACTIONS_RAW_FIELDS, row, strict=True)),
    )

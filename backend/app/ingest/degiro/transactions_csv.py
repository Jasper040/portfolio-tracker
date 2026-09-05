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


class MalformedRow(Exception):
    """A row could not be parsed. Carries the file and line so it can be found.

    With 112 rows of 17 columns, an error that does not say *where* it failed turns
    a two-minute fix into a bisect.
    """


def parse_transactions_csv(path: Path) -> list[NormalisedRow]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        assert_header(header, TRANSACTIONS_HEADER, path.name)
        # Keep the physical line number: blank-row filtering makes a later enumerate()
        # disagree with the file, and a parse error must name the line you can go read.
        numbered = [
            (line_no, row)
            for line_no, row in enumerate(reader, start=2)
            if any(cell.strip() for cell in row)
        ]

    raw_rows = [row for _, row in numbered]

    # Validated before ref_inputs is built: that comprehension also indexes by
    # TxnCol, so a short row would otherwise raise a bare, uncaught IndexError here
    # rather than reaching the per-row try/except below.
    for line_no, row in numbered:
        if len(row) != len(TRANSACTIONS_RAW_FIELDS):
            raise MalformedRow(
                f"{path.name} line {line_no}: expected {len(TRANSACTIONS_RAW_FIELDS)} "
                f"columns, got {len(row)}"
            )

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

    parsed: list[NormalisedRow] = []
    for (line_no, row), ref in zip(numbered, refs, strict=True):
        try:
            parsed.append(_to_normalised(row, ref))
        except (ValueError, IndexError) as exc:
            raise MalformedRow(f"{path.name} line {line_no}: {exc}") from exc
    return parsed


def _to_normalised(row: list[str], source_ref: str) -> NormalisedRow:
    if len(row) != len(TRANSACTIONS_RAW_FIELDS):
        raise ValueError(
            f"expected {len(TRANSACTIONS_RAW_FIELDS)} columns, got {len(row)}"
        )

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

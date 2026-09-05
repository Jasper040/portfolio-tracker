"""DeGiro CSV dialect: Dutch locale plus verified positional column layouts.

The header row in every DeGiro export is misaligned against the data. A currency
column precedes its amount, but the header's blank placeholder sits on the wrong
side, so `Account.csv`'s header reads `...,FX,Change,,Balance,,Order Id` while the
data is `...,fx,change_ccy,change,balance_ccy,balance,order_id`.

Mapping by header name therefore shifts every column silently. Columns are mapped
positionally, and `assert_header` fails the import loudly if DeGiro ever changes
the export shape these indices were verified against.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal


class UnexpectedHeader(Exception):
    """The export header no longer matches the layout these indices assume."""


TRANSACTIONS_HEADER = (
    "Date,Time,Product,ISIN,Reference exchange,Venue,Quantity,Price,,"
    "Local value,,Value EUR,Exchange rate,AutoFX Fee,"
    "Transaction and/or third party fees EUR,Total EUR,Order ID"
)

ACCOUNT_HEADER = "Date,Time,Value date,Product,ISIN,Description,FX,Change,,Balance,,Order Id"

PORTFOLIO_HEADER = "Product,Symbol/ISIN,Amount,Closing,Local value,,Value in EUR"

# The header's two blank names would collide as dict keys and silently drop a column
# from `raw_json`. These are the same fields in the same order, with the unnamed
# currency columns given the names the data actually puts there.
TRANSACTIONS_RAW_FIELDS = [
    "Date",
    "Time",
    "Product",
    "ISIN",
    "Reference exchange",
    "Venue",
    "Quantity",
    "Price",
    "Local value currency",
    "Local value",
    "Value EUR currency",
    "Value EUR",
    "Exchange rate",
    "AutoFX Fee",
    "Transaction and/or third party fees EUR",
    "Total EUR",
    "Order ID",
]


class TxnCol:
    """Verified positional indices for Transactions.csv."""

    DATE = 0
    TIME = 1
    PRODUCT = 2
    ISIN = 3
    REF_EXCHANGE = 4
    VENUE = 5
    QUANTITY = 6
    PRICE = 7
    LOCAL_CCY = 8
    LOCAL_VALUE = 9
    VALUE_EUR_CCY = 10
    VALUE_EUR = 11
    FX_RATE = 12
    AUTOFX_FEE = 13
    TXN_FEE = 14
    TOTAL_EUR = 15
    ORDER_ID = 16


def assert_header(actual: list[str], expected: str, filename: str) -> None:
    joined = ",".join(actual)
    if joined != expected:
        raise UnexpectedHeader(
            f"{filename} header changed.\n  expected: {expected}\n  actual:   {joined}\n"
            "Column indices in dialect.py were verified against the expected header. "
            "Re-verify them before importing."
        )


def parse_decimal(raw: str) -> Decimal:
    """Parse a Dutch-locale number: '.' groups thousands, ',' is the decimal point."""
    text = raw.strip()
    if not text:
        raise ValueError("cannot parse an empty string as a decimal")
    return Decimal(text.replace(".", "").replace(",", "."))


def parse_optional_decimal(raw: str) -> Decimal | None:
    """Blank means absent, which is not the same as zero."""
    return parse_decimal(raw) if raw.strip() else None


def parse_dutch_date(raw: str) -> date:
    return datetime.strptime(raw.strip(), "%d-%m-%Y").date()

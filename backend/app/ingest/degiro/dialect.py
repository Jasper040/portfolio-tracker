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

import re
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
# from `raw_json`. These are the same fields in the same order. Each unnamed currency
# column is named for the amount it follows -- "Price currency" follows "Price" at
# index 7, "Local value currency" follows "Local value" at index 9 -- matching the
# verified positional layout (design doc Sec 3.1: 8 price_ccy, 9 local_value,
# 10 local_ccy, 11 value_eur). A tuple, not a list: this is module-level shared state
# acting as a schema, not a mutable collection.
TRANSACTIONS_RAW_FIELDS: tuple[str, ...] = (
    "Date",
    "Time",
    "Product",
    "ISIN",
    "Reference exchange",
    "Venue",
    "Quantity",
    "Price",
    "Price currency",
    "Local value",
    "Local value currency",
    "Value EUR",
    "Exchange rate",
    "AutoFX Fee",
    "Transaction and/or third party fees EUR",
    "Total EUR",
    "Order ID",
)


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
    PRICE_CCY = 8
    LOCAL_VALUE = 9
    LOCAL_CCY = 10
    VALUE_EUR = 11
    FX_RATE = 12
    AUTOFX_FEE = 13
    TXN_FEE = 14
    TOTAL_EUR = 15
    ORDER_ID = 16


# Optional sign; digits either ungrouped or grouped by "." in threes; optional ","
# decimal part. Both grouped ("10.623,75") and ungrouped ("778,25") forms occur in
# real exports, and quantities arrive as bare integers ("2", "-5").
_DUTCH_NUMBER = re.compile(r"^-?(?:\d+|\d{1,3}(?:\.\d{3})+)(?:,\d+)?$")


def assert_header(actual: list[str], expected: str, filename: str) -> None:
    joined = ",".join(actual)
    if joined != expected:
        raise UnexpectedHeader(
            f"{filename} header changed.\n  expected: {expected}\n  actual:   {joined}\n"
            "Column indices in dialect.py were verified against the expected header. "
            "Re-verify them before importing."
        )


def parse_decimal(raw: str) -> Decimal:
    """Parse a Dutch-locale number: '.' groups thousands, ',' is the decimal point.

    The shape is validated before the separators are swapped. Without that check an
    English-formatted cell like "778.25" passes straight through the replacements
    and returns Decimal("103650") — a hundredfold error, silent, in a money parser.
    Failing loudly on an unexpected shape is the entire point of this function.

    One ambiguity necessarily remains: "1.036" is read as 1036, the Dutch reading.
    A file mixing English decimals with Dutch headers could still be misread there,
    which is what `assert_header` guards against upstream.
    """
    text = raw.strip()
    if not text:
        raise ValueError("cannot parse an empty string as a decimal")
    if not _DUTCH_NUMBER.match(text):
        raise ValueError(f"not a Dutch-formatted number: {raw!r}")
    return Decimal(text.replace(".", "").replace(",", "."))


def parse_optional_decimal(raw: str) -> Decimal | None:
    """Blank means absent, which is not the same as zero."""
    return parse_decimal(raw) if raw.strip() else None


def parse_dutch_date(raw: str) -> date:
    return datetime.strptime(raw.strip(), "%d-%m-%Y").date()


class AcctCol:
    """Verified positional indices for Account.csv.

    The header names 12 fields but leaves two of them blank, and the blanks sit on
    the wrong side of the amounts they belong to. The data is really
    `date, time, value_date, product, isin, description, fx, change_ccy, change,
    balance_ccy, balance, order_id` -- a currency column precedes each amount, while
    the header reads `...,FX,Change,,Balance,,Order Id`. Mapping by name would put
    the currency where the amount belongs on every single row.
    """

    DATE = 0
    TIME = 1
    VALUE_DATE = 2
    PRODUCT = 3
    ISIN = 4
    DESCRIPTION = 5
    FX = 6
    CHANGE_CCY = 7
    CHANGE = 8
    BALANCE_CCY = 9
    BALANCE = 10
    ORDER_ID = 11


# Same fields, same order, with the two unnamed currency columns given the name of
# the amount they precede -- so `raw_json` keeps all twelve instead of silently
# collapsing the two blanks into a single key.
ACCOUNT_RAW_FIELDS: tuple[str, ...] = (
    "Date",
    "Time",
    "Value date",
    "Product",
    "ISIN",
    "Description",
    "FX",
    "Change currency",
    "Change",
    "Balance currency",
    "Balance",
    "Order Id",
)

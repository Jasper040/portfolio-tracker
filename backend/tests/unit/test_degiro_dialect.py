from datetime import date
from decimal import Decimal

import pytest

from app.ingest.degiro.dialect import (
    TRANSACTIONS_HEADER,
    TRANSACTIONS_RAW_FIELDS,
    TxnCol,
    UnexpectedHeader,
    assert_header,
    parse_decimal,
    parse_dutch_date,
    parse_optional_decimal,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("778,25", Decimal("778.25")),
        ("-1.322,44", Decimal("-1322.44")),
        ("155,6000", Decimal("155.6000")),
        ("1,2150", Decimal("1.2150")),
        ("0,00", Decimal("0.00")),
        ("10.623,75", Decimal("10623.75")),
    ],
)
def test_parses_dutch_decimals(raw: str, expected: Decimal) -> None:
    assert parse_decimal(raw) == expected


def test_blank_decimal_is_none_not_zero() -> None:
    """A blank fee means 'the fee is on another row', not 'the fee was zero'."""
    assert parse_optional_decimal("") is None
    assert parse_optional_decimal("   ") is None
    assert parse_optional_decimal("0,00") == Decimal("0.00")


def test_parses_dutch_dates() -> None:
    assert parse_dutch_date("27-07-2026") == date(2026, 7, 27)


def test_header_guard_accepts_the_verified_header() -> None:
    assert_header(TRANSACTIONS_HEADER.split(","), TRANSACTIONS_HEADER, "Transactions.csv")


def test_header_guard_rejects_a_changed_header() -> None:
    """If DeGiro changes the export, fail loudly rather than shift every column."""
    changed = TRANSACTIONS_HEADER.replace("Order ID", "Order Reference").split(",")
    with pytest.raises(UnexpectedHeader):
        assert_header(changed, TRANSACTIONS_HEADER, "Transactions.csv")


def test_column_indices_match_the_data_not_the_header() -> None:
    """Header says index 8 is unnamed; the data puts the local currency there."""
    assert TxnCol.PRICE == 7
    assert TxnCol.LOCAL_CCY == 8
    assert TxnCol.LOCAL_VALUE == 9
    assert TxnCol.VALUE_EUR == 11
    assert TxnCol.TOTAL_EUR == 15
    assert TxnCol.ORDER_ID == 16


@pytest.mark.parametrize(
    "raw",
    ["778.25", "abc", "-", "(1.322,44)", "1.036.50", ".50", "1 036,50", "--1,00"],
)
def test_rejects_input_that_is_not_a_dutch_number(raw: str) -> None:
    """A silent hundredfold error is the worst outcome for a money parser:
    "778.25" must raise, not quietly become Decimal("103650")."""
    with pytest.raises(ValueError):
        parse_decimal(raw)


def test_parses_bare_integers() -> None:
    """Quantities arrive with no decimal part at all."""
    assert parse_decimal("2") == Decimal("2")
    assert parse_decimal("-5") == Decimal("-5")


def test_raw_fields_is_a_stable_17_column_map() -> None:
    """TRANSACTIONS_RAW_FIELDS exists to stop a column being silently dropped from
    raw_json. Nothing else in the suite would catch a reorder, duplicate or deletion."""
    assert len(TRANSACTIONS_RAW_FIELDS) == 17
    assert len(set(TRANSACTIONS_RAW_FIELDS)) == 17
    assert all(name.strip() for name in TRANSACTIONS_RAW_FIELDS)
    assert TRANSACTIONS_RAW_FIELDS[TxnCol.LOCAL_CCY] == "Local value currency"
    assert TRANSACTIONS_RAW_FIELDS[TxnCol.VALUE_EUR_CCY] == "Value EUR currency"
    assert TRANSACTIONS_RAW_FIELDS[TxnCol.TOTAL_EUR] == "Total EUR"
    assert TRANSACTIONS_RAW_FIELDS[TxnCol.ORDER_ID] == "Order ID"

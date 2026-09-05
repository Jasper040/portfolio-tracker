from decimal import Decimal
from pathlib import Path

import pytest

from app.ingest.degiro.dialect import TRANSACTIONS_HEADER, UnexpectedHeader
from app.ingest.degiro.transactions_csv import MalformedRow, parse_transactions_csv

GOLDEN = Path(__file__).parents[1] / "golden" / "degiro_transactions_golden.csv"


@pytest.fixture(scope="module")
def rows() -> list:
    return parse_transactions_csv(GOLDEN)


def test_parses_every_data_row(rows: list) -> None:
    assert len(rows) == 13


def test_quoted_product_name_with_a_comma_survives(rows: list) -> None:
    row = next(r for r in rows if r.isin == "US0000000002")
    assert row.product_name == "TEST COMMA CORP, INC."


def test_buy_and_sell_are_derived_from_quantity_sign(rows: list) -> None:
    buy = next(r for r in rows if r.order_ref == "aaaa0001-0000-0000-0000-000000000001")
    sell = next(r for r in rows if r.order_ref == "bbbb0001-0000-0000-0000-000000000003")
    assert buy.txn_type == "BUY"
    assert sell.txn_type == "SELL"


def test_blank_fee_is_zero_not_none_on_the_row(rows: list) -> None:
    """A blank fee means the commission sits on a sibling fill; this row paid none."""
    fills = [r for r in rows if r.order_ref == "bbbb0001-0000-0000-0000-000000000003"]
    assert sorted(f.fee_base for f in fills) == [Decimal("-3.00"), Decimal("0.00")]


def test_net_base_is_broker_truth_even_when_arithmetic_disagrees(rows: list) -> None:
    """Row 8's components sum to 90.36; DeGiro says 90.35. Store 90.35."""
    row = next(r for r in rows if r.order_ref == "aaaa0003-0000-0000-0000-000000000006")
    assert row.net_base == Decimal("90.35")
    assert row.net_base != row.gross_base_components_sum


def test_eur_rows_have_no_fx_rate(rows: list) -> None:
    row = next(r for r in rows if r.isin == "NL0000000001")
    assert row.fx_rate is None
    assert row.currency_local == "EUR"


def test_foreign_currency_rows_keep_the_broker_rate_verbatim(rows: list) -> None:
    row = next(r for r in rows if r.isin == "AU0000000001")
    assert row.currency_local == "AUD"
    assert row.fx_rate == Decimal("1.6500")


def test_identical_fill_rows_become_two_distinct_transactions(rows: list) -> None:
    fills = [r for r in rows if r.order_ref == "cccc0002-0000-0000-0000-000000000005"]
    assert len(fills) == 2
    assert fills[0].source_ref != fills[1].source_ref


def test_blank_order_id_rows_are_parsed_not_dropped(rows: list) -> None:
    """The split pair is suppressed in M0b, but M0a must still ingest it."""
    split = [r for r in rows if r.isin == "NL0000000003" and not r.order_ref]
    assert len(split) == 2
    assert all(r.is_economic for r in split)


def test_raw_row_is_preserved(rows: list) -> None:
    row = rows[0]
    assert row.raw["Total EUR"] == "-102,00"


def test_raw_row_keeps_every_column_including_the_unnamed_ones(rows: list) -> None:
    """The header has two blank names. Keying the raw dict off it would collapse them
    into one entry and silently lose a column — the exact class of bug raw_json exists
    to catch. Both currency columns must survive under distinct names."""
    row = next(r for r in rows if r.isin == "AU0000000001")
    assert len(row.raw) == 17
    assert row.raw["Local value currency"] == "AUD"
    assert row.raw["Value EUR currency"] == "AUD"
    assert row.raw["Local value"] == "-50,00"
    assert row.raw["Value EUR"] == "-30,30"


def test_rejects_a_file_whose_header_changed(tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text("Date,Time,Product\n06-01-2025,09:00,X\n", encoding="utf-8")
    with pytest.raises(UnexpectedHeader):
        parse_transactions_csv(bad)


def test_column_mapping_is_pinned_for_local_and_base_values(rows: list) -> None:
    """A transposed LOCAL_VALUE/VALUE_EUR index would pass every other test here: the
    broker-truth test asserts only an inequality, which survives a swap. Positional
    correctness is this parser's entire reason to exist, so pin it to real values."""
    aud = next(r for r in rows if r.isin == "AU0000000001")
    assert aud.gross_local == Decimal("-50.00")  # Local value, in AUD
    assert aud.value_base == Decimal("-30.30")  # Value EUR
    assert aud.net_base == Decimal("-32.38")  # Total EUR


def test_a_short_row_reports_its_line_number(tmp_path: Path) -> None:
    """A bare IndexError across 112 rows of 17 columns locates nothing."""
    bad = tmp_path / "short.csv"
    bad.write_text(TRANSACTIONS_HEADER + "\n06-01-2025,09:00,X\n", encoding="utf-8")
    with pytest.raises(MalformedRow) as exc:
        parse_transactions_csv(bad)
    assert "line 2" in str(exc.value)


def test_a_malformed_number_reports_its_line_number(tmp_path: Path) -> None:
    lines = GOLDEN.read_text(encoding="utf-8").strip().split("\n")
    lines[1] = lines[1].replace('"10,0000"', '"10.0000"')  # English decimal
    bad = tmp_path / "badnum.csv"
    bad.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(MalformedRow) as exc:
        parse_transactions_csv(bad)
    assert "line 2" in str(exc.value)

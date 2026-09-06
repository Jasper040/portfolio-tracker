"""Account.csv classification.

Design doc Sec 3.2 lists roughly twenty kinds of row across 252 distinct free-text
descriptions, mixing Dutch and English in one export. Sec 3.3 is the reason this is
the highest-risk parser in the project: 256 of 785 rows look exactly like deposits
and withdrawals and are not. They carry real signed euro amounts and plausible
running balances, so only the description distinguishes them -- classify them as
external cash flows and MWR becomes meaningless.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

from app.ingest.degiro.account_csv import AccountAction, classify, parse_account_csv
from app.ingest.degiro.dialect import UnexpectedHeader

GOLDEN = Path(__file__).parents[1] / "golden" / "degiro_account_golden.csv"

D = Decimal


class TestClassification:
    @pytest.mark.parametrize(
        "description,expected",
        [
            ("iDEAL Deposit", "DEPOSIT"),
            ("SEPA Instant Terugstorting", "WITHDRAWAL"),
            ("Dividend", "DIVIDEND"),
            ("Dividendbelasting", "DIVIDEND_TAX"),
            ("Valuta Creditering", "FX_CONVERT"),
            ("Valuta Debitering", "FX_CONVERT"),
            ("Transactiebelasting Frankrijk", "TAX"),
            ("DEGIRO Aansluitingskosten 2025 (Euronext Amsterdam)", "FEE"),
            ("Rente", "INTEREST"),
            ("Flatex Interest Income", "INTEREST"),
            ("Inkomsten uit Securities Lending - December", "SECURITIES_LENDING"),
            ("SPLIT AANPASSING: TEST SPLIT NV", "CORPORATE_ACTION"),
            ("PRODUCTWIJZIGING : TEST COMMA CORP. INC.", "CORPORATE_ACTION"),
        ],
    )
    def test_maps_each_kept_pattern_to_its_type(self, description: str, expected: str) -> None:
        action = classify(description, D("-1.00"))
        assert action.txn_type == expected
        assert action.keep is True

    @pytest.mark.parametrize(
        "description",
        [
            "Koop 100 @ 1,0000 EUR",
            "Verkoop 10 @ 10,0000 EUR",
            "DEGIRO Transactiekosten en/of kosten van derden",
            "Degiro Cash Sweep Transfer",
            "Overboeking naar uw geldrekening bij flatexDEGIRO Bank",
            "Overboeking van uw geldrekening bij flatexDEGIRO Bank",
            "Reservation iDEAL",
        ],
    )
    def test_drops_duplicates_and_internal_transfers(self, description: str) -> None:
        assert classify(description, D("-1.00")).keep is False

    def test_dividendbelasting_is_not_a_dividend(self) -> None:
        """The trap that prefix matching invites: 'Dividendbelasting' starts with
        'Dividend'. Matching in the wrong order books 38 withholding rows as income
        and inflates dividends received by every cent of tax withheld."""
        assert classify("Dividendbelasting", D("-1.00")).txn_type == "DIVIDEND_TAX"
        assert classify("Dividend", D("1.00")).txn_type == "DIVIDEND"

    def test_sepa_terugstorting_is_not_the_flatex_one(self) -> None:
        """Both descriptions contain 'terugstorting' and both are real outflows,
        but only one is named as a direction: `flatex terugstorting` is typed by the
        sign of its amount, so the same wording could equally book a deposit."""
        assert classify("SEPA Instant Terugstorting", D("-75.00")).txn_type == "WITHDRAWAL"
        assert classify("flatex terugstorting", D("-8000.00")).txn_type == "WITHDRAWAL"

    def test_reservation_ideal_is_not_an_ideal_deposit(self) -> None:
        # 22 reserve/release rows net to exactly EUR 0.00; only 11 rows are real.
        assert classify("Reservation iDEAL", D("-250.00")).keep is False
        assert classify("iDEAL Deposit", D("1000.00")).txn_type == "DEPOSIT"

    def test_an_unknown_description_is_reported_not_guessed(self) -> None:
        """A description this parser has not been taught must not be silently
        dropped or silently kept. Sec 3.2 says the field is free text and DeGiro
        has already changed its wording once."""
        action = classify("Iets Geheel Nieuws", D("1.00"))
        assert action.keep is False
        assert action.txn_type is None
        assert action.recognised is False

    def test_recognises_every_description_in_the_golden_file(self) -> None:
        rows = parse_account_csv(GOLDEN)
        unrecognised = [r for r in rows if not r.action.recognised]
        assert unrecognised == []


class TestParsing:
    def test_reads_every_row_of_the_golden_file(self) -> None:
        assert len(parse_account_csv(GOLDEN)) == 24

    def test_keeps_exactly_the_genuine_rows(self) -> None:
        """Asserting the whole multiset, not a count: a count passes just as well
        when one type is dropped and another double-counted."""
        kept = Counter(
            row.action.txn_type for row in parse_account_csv(GOLDEN) if row.action.keep
        )
        assert kept == Counter(
            {
                "CORPORATE_ACTION": 4,
                "FX_CONVERT": 2,
                "INTEREST": 2,
                "DEPOSIT": 2,
                "WITHDRAWAL": 2,
                "TAX": 1,
                "DIVIDEND": 1,
                "DIVIDEND_TAX": 1,
                "SECURITIES_LENDING": 1,
                "FEE": 1,
            }
        )
        # 24 rows in, 7 dropped as trade duplicates or internal transfers.
        assert sum(kept.values()) == 17

    def test_only_the_genuine_flows_survive_the_sweep_trap(self) -> None:
        """Sec 3.3: the sweep rows carry real amounts and plausible balances. If any
        of them leaked through, these would be wrong and MWR would be too.

        The flatex pair joins the iDEAL deposit and the SEPA withdrawal because it
        is a movement to the owner's own bank, not within the broker -- typed by
        sign, since the description does not say which way the money went.
        """
        rows = parse_account_csv(GOLDEN)
        deposits = [r for r in rows if r.action.txn_type == "DEPOSIT"]
        withdrawals = [r for r in rows if r.action.txn_type == "WITHDRAWAL"]
        assert sorted(r.change for r in deposits) == [D("800.00"), D("1000.00")]
        assert sorted(r.change for r in withdrawals) == [D("-800.00"), D("-75.00")]

    def test_parses_dutch_amounts_and_dates(self) -> None:
        row = next(r for r in parse_account_csv(GOLDEN) if r.action.txn_type == "DEPOSIT")
        assert row.change == D("1000.00")
        assert row.change_currency == "EUR"
        assert row.trade_date.isoformat() == "2025-01-10"

    def test_carries_the_order_id_on_trade_linked_rows(self) -> None:
        """Sec 3.6 joins the two files on order id, so it has to survive parsing."""
        fees = [
            r
            for r in parse_account_csv(GOLDEN)
            if r.description.startswith("DEGIRO Transactiekosten")
        ]
        assert [f.order_ref for f in fees] == ["aaaa0006-0000-0000-0000-000000000009"]

    def test_keeps_the_isin_on_corporate_action_rows(self) -> None:
        """Sec 3.4 joins on (date, isin, abs(amount)), so a corporate-action row
        without its ISIN cannot be matched to the trade pair it explains."""
        actions = [r for r in parse_account_csv(GOLDEN) if r.action.txn_type == "CORPORATE_ACTION"]
        assert len(actions) == 4
        assert all(r.isin for r in actions)

    def test_preserves_every_raw_cell(self) -> None:
        row = parse_account_csv(GOLDEN)[0]
        assert row.raw["Description"].startswith("PRODUCTWIJZIGING")
        assert set(row.raw) == {
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
        }

    def test_rejects_a_file_whose_header_has_moved(self, tmp_path: Path) -> None:
        bad = tmp_path / "account.csv"
        bad.write_text("Date,Time,Something Else\n01-01-2025,10:00,x\n", encoding="utf-8")
        # Specifically UnexpectedHeader: a blind `Exception` would also pass if the
        # parser died on an IndexError, which is the failure this guards against.
        with pytest.raises(UnexpectedHeader):
            parse_account_csv(bad)


class TestActionShape:
    def test_a_dropped_row_never_carries_a_type(self) -> None:
        # Keeping a type on a dropped row invites a later change to import it by
        # accident, which is exactly the cash-sweep failure.
        for description in ("Degiro Cash Sweep Transfer", "Koop 1 @ 1,00 EUR"):
            action: AccountAction = classify(description, D("-1.00"))
            assert action.keep is False
            assert action.txn_type is None

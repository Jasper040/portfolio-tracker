"""Turning classified Account.csv rows into ledger rows.

`Transactions.csv` is authoritative for trades; this file is authoritative for
everything else (design doc Sec 6.2), so without this step the ledger has no
dividends, no deposits and no fees -- it is a trade blotter, not a ledger.

The decision this module encodes is what `net_base` means on a row DeGiro booked
in a foreign currency. It means euros, always: a row that moved AUD moved no euros,
and the euros arrive later on the paired `Valuta Creditering` row. Writing the AUD
figure into a euro column would make `sum(net_base)` silently add AUD to EUR.
"""

from __future__ import annotations

import random
from decimal import Decimal
from pathlib import Path

from app.ingest.degiro.account_csv import normalise_account_rows, parse_account_csv

GOLDEN = Path(__file__).parents[1] / "golden" / "degiro_account_golden.csv"

D = Decimal


def _rows() -> list:
    return normalise_account_rows(parse_account_csv(GOLDEN))


class TestSelection:
    def test_keeps_only_the_genuine_rows(self) -> None:
        """The seven dropped rows are trade duplicates and internal transfers. A
        cash sweep booked as a deposit makes MWR meaningless (Sec 3.3)."""
        assert len(_rows()) == 17

    def test_drops_the_trade_duplicates_that_transactions_csv_owns(self) -> None:
        assert not [row for row in _rows() if row.txn_type == "TRADE"]

    def test_carries_the_classified_type_onto_the_ledger_row(self) -> None:
        types = {row.txn_type for row in _rows()}
        assert "DIVIDEND" in types
        assert "DIVIDEND_TAX" in types
        assert "DEPOSIT" in types


class TestCurrency:
    def _dividend(self):
        return next(row for row in _rows() if row.txn_type == "DIVIDEND")

    def test_a_foreign_row_moves_no_euros(self) -> None:
        """The AUD dividend. DeGiro books EUR later, on its own FX row, so there is
        no euro figure on this one to record -- and inventing one is worse than
        recording a zero, because the invented one looks like broker truth."""
        dividend = self._dividend()
        assert dividend.currency_local == "AUD"
        assert dividend.gross_local == D("12.50")
        assert dividend.net_base == D("0.00")

    def test_a_euro_row_carries_its_amount_as_broker_truth(self) -> None:
        deposit = next(row for row in _rows() if row.txn_type == "DEPOSIT")
        assert deposit.currency_local == "EUR"
        assert deposit.net_base == D("1000.00")
        assert deposit.gross_local == D("1000.00")

    def test_summing_net_base_stays_in_euros(self) -> None:
        """The property the zero exists to protect: every non-zero `net_base` in
        the ledger is denominated in the same currency, so the sum is meaningful."""
        rows = _rows()
        moved = [row for row in rows if row.net_base != 0]
        assert {row.currency_local for row in moved} == {"EUR"}


class TestAmounts:
    def test_does_not_duplicate_the_amount_into_the_fee_column(self) -> None:
        """A standalone fee row is a transaction whose amount IS the fee. Copying it
        into `fee_base` as well would double it in any `net_base + fee_base` sum."""
        fee = next(row for row in _rows() if row.txn_type == "FEE")
        assert fee.net_base == D("-2.50")
        assert fee.fee_base == D("0.00")
        assert fee.tax_base == D("0.00")

    def test_a_cash_row_has_no_quantity_or_price(self) -> None:
        """Nothing in Account.csv is a share movement, so a quantity here would be
        a fabricated fact -- and M1 groups fills on quantity."""
        deposit = next(row for row in _rows() if row.txn_type == "DEPOSIT")
        assert deposit.quantity is None
        assert deposit.price_local is None


class TestProvenance:
    def test_keeps_the_isin_and_product_where_the_row_has_one(self) -> None:
        dividend = next(row for row in _rows() if row.txn_type == "DIVIDEND")
        assert dividend.isin == "AU0000000003"
        assert dividend.product_name == "TEST AUSSIE LTD"

    def test_settle_date_comes_from_the_value_date_column(self) -> None:
        deposit = next(row for row in _rows() if row.txn_type == "DEPOSIT")
        assert deposit.settle_date is not None
        assert deposit.settle_date.isoformat() == "2025-01-10"

    def test_keeps_every_raw_cell_for_the_browser(self) -> None:
        assert all(row.raw for row in _rows())
        assert "Description" in _rows()[0].raw


class TestSourceRefs:
    def test_every_ref_is_distinct(self) -> None:
        rows = _rows()
        assert len({row.source_ref for row in rows}) == len(rows)

    def test_a_reordered_export_produces_the_same_refs(self) -> None:
        """Same guarantee as the trade file: DeGiro's row order is not stable, and
        a re-export that shuffled rows must re-import as zero new rows."""
        parsed = parse_account_csv(GOLDEN)
        shuffled = list(parsed)
        random.Random(7).shuffle(shuffled)
        assert {row.source_ref for row in normalise_account_rows(parsed)} == {
            row.source_ref for row in normalise_account_rows(shuffled)
        }

    def test_does_not_collide_with_a_trade_ref(self) -> None:
        """Both files feed one `UNIQUE(source, source_ref)` constraint, so a
        collision would silently drop a row rather than raise."""
        from app.ingest.degiro.transactions_csv import parse_transactions_csv

        trades = parse_transactions_csv(
            Path(__file__).parents[1] / "golden" / "degiro_transactions_golden.csv"
        )
        account = _rows()
        assert {row.source_ref for row in trades}.isdisjoint({row.source_ref for row in account})

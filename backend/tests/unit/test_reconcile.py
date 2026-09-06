"""Cross-file reconciliation (design doc Sec 3.6).

Built from hand-made rows rather than the golden files, because the two goldens are
not yet a mutually reconciling pair: `degiro_transactions_golden.csv` gives one
order (cccc0002) no commission at all, which is realistic as a Transactions.csv
quirk but means a matching Account.csv cannot satisfy "exactly one commission per
order". Making them a consistent pair is worth doing and is tracked separately; the
opt-in realdata suite covers the integration against the real exports meanwhile.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from app.ingest.base import NormalisedRow
from app.ingest.degiro.account_csv import AccountRow, classify
from app.ingest.degiro.portfolio_csv import PortfolioSnapshot, parse_portfolio_csv
from app.ingest.reconcile import combined_eur_cash, reconcile

D = Decimal
GOLDEN_PORTFOLIO = Path(__file__).parents[1] / "golden" / "degiro_portfolio_golden.csv"


def trade(order_ref: str | None, fee: str) -> NormalisedRow:
    return NormalisedRow(
        source="degiro",
        source_ref=f"ref-{order_ref}-{fee}",
        txn_type="BUY",
        trade_date=date(2025, 1, 1),
        net_base=D("-100.00"),
        fee_base=D(fee),
        tax_base=D("0.00"),
        raw={},
        order_ref=order_ref,
    )


def account_row(description: str, change: str | None, currency: str | None = "EUR",
                order_ref: str | None = None) -> AccountRow:
    return AccountRow(
        line_no=1,
        trade_date=date(2025, 1, 1),
        trade_time="10:00",
        value_date=date(2025, 1, 1),
        product=None,
        isin=None,
        description=description,
        fx_rate=None,
        change=None if change is None else D(change),
        change_currency=currency,
        balance=None,
        balance_currency=currency,
        order_ref=order_ref,
        action=classify(description),
        raw={},
    )


def commission(order_ref: str, amount: str) -> AccountRow:
    return account_row(
        "DEGIRO Transactiekosten en/of kosten van derden", amount, order_ref=order_ref
    )


class TestGreenReport:
    TRADES = [trade("order-1", "-2.00"), trade("order-2", "-3.00"), trade(None, "0.00")]
    ACCOUNT = [commission("order-1", "-2.00"), commission("order-2", "-3.00")]

    def test_all_three_file_invariants_pass(self) -> None:
        report = reconcile(self.TRADES, self.ACCOUNT)
        assert report.ok
        assert report.failures == ()
        assert len(report.invariants) == 3

    def test_the_cash_invariant_is_only_added_when_a_snapshot_is_given(self) -> None:
        # The first three compare the exports to each other and need nothing else,
        # so a caller without Portfolio.csv still gets a usable report.
        assert len(reconcile(self.TRADES, self.ACCOUNT).invariants) == 3
        with_cash = reconcile(
            self.TRADES, self.ACCOUNT, PortfolioSnapshot(cash_base=D("-5.00"), positions=())
        )
        assert len(with_cash.invariants) == 4


class TestFailuresAreNamed:
    def test_reports_every_invariant_rather_than_raising_on_the_first(self) -> None:
        """M0's outcome is a green report, so a red one has to say which line is red.
        Raising on the first failure turns a diagnosis back into a bisect."""
        report = reconcile([trade("order-1", "-2.00")], [commission("order-9", "-9.99")])
        assert not report.ok
        assert len(report.invariants) == 3
        assert {i.name for i in report.failures} == {
            "commission totals agree between the two files",
            "order ids match in both directions",
        }

    def test_catches_a_commission_total_that_disagrees(self) -> None:
        report = reconcile([trade("order-1", "-2.00")], [commission("order-1", "-3.00")])
        names = {i.name for i in report.failures}
        assert "commission totals agree between the two files" in names

    def test_tolerates_a_cent_of_broker_rounding(self) -> None:
        # Sec 5.4: broker arithmetic disagrees with itself by a cent on 14 of 112
        # rows, so an aggregate reconciles to 0.50, not to the cent.
        report = reconcile([trade("order-1", "-2.00")], [commission("order-1", "-2.01")])
        assert report.ok

    def test_catches_an_order_with_two_commission_rows(self) -> None:
        report = reconcile(
            [trade("order-1", "-2.00")],
            [commission("order-1", "-1.00"), commission("order-1", "-1.00")],
        )
        names = {i.name for i in report.failures}
        assert "exactly one commission row per order" in names

    def test_names_the_orphans_it_found(self) -> None:
        report = reconcile([trade("order-1", "-2.00")], [commission("order-2", "-2.00")])
        orphans = next(
            i for i in report.invariants if i.name == "order ids match in both directions"
        )
        assert "order-1" in orphans.detail
        assert "order-2" in orphans.detail


class TestCombinedCash:
    def test_excludes_the_sweep_between_the_two_pockets(self) -> None:
        """Portfolio.csv reports DeGiro cash and flatex cash as ONE line, so a
        transfer between them must not move the total."""
        rows = [
            account_row("iDEAL Deposit", "1000.00"),
            account_row("Degiro Cash Sweep Transfer", "-400.00"),
            account_row("Degiro Cash Sweep Transfer", "400.00"),
        ]
        assert combined_eur_cash(rows) == D("1000.00")

    def test_counts_a_lone_sweep_as_zero_rather_than_as_a_withdrawal(self) -> None:
        rows = [
            account_row("iDEAL Deposit", "1000.00"),
            account_row("Degiro Cash Sweep Transfer", "-400.00"),
        ]
        assert combined_eur_cash(rows) == D("1000.00")

    def test_ignores_rows_in_another_currency(self) -> None:
        # A USD dividend reaches EUR cash through a Valuta Creditering row, not
        # directly. Counting it twice is the failure this guards.
        rows = [
            account_row("Dividend", "80.02", currency="USD"),
            account_row("Valuta Creditering", "74.10", currency="EUR"),
        ]
        assert combined_eur_cash(rows) == D("74.10")

    def test_ignores_rows_with_no_amount(self) -> None:
        # The `Overboeking` mirror leg carries its amount in the description text
        # only, so it contributes nothing and needs no special case.
        rows = [
            account_row("iDEAL Deposit", "10.00"),
            account_row(
                "Overboeking naar uw geldrekening bij flatexDEGIRO Bank 384,40", None, None
            ),
        ]
        assert combined_eur_cash(rows) == D("10.00")

    def test_counts_dropped_rows_that_still_moved_cash(self) -> None:
        """Dropped from the LEDGER is not the same as excluded from CASH.

        `Koop`/`Verkoop` rows are dropped as trade duplicates because
        Transactions.csv is authoritative, but their euro amounts are still what
        moved through the account -- Sec 6.2 says exactly that.
        """
        rows = [
            account_row("iDEAL Deposit", "1000.00"),
            account_row("Koop 1 @ 10,00 EUR", "-10.00"),
        ]
        assert all(not r.action.keep for r in rows[1:])
        assert combined_eur_cash(rows) == D("990.00")


class TestPortfolioSnapshot:
    def test_reads_the_combined_cash_line(self) -> None:
        assert parse_portfolio_csv(GOLDEN_PORTFOLIO).cash_base == D("-398.77")

    def test_reads_positions_but_not_the_cash_line_as_one(self) -> None:
        snapshot = parse_portfolio_csv(GOLDEN_PORTFOLIO)
        assert len(snapshot.positions) == 2
        assert snapshot.quantity_of("NL0000000001") == D("100")

    def test_reports_zero_for_an_instrument_not_held(self) -> None:
        assert parse_portfolio_csv(GOLDEN_PORTFOLIO).quantity_of("XX0000000000") == D("0")

    def test_handles_a_product_name_containing_a_comma(self) -> None:
        snapshot = parse_portfolio_csv(GOLDEN_PORTFOLIO)
        assert snapshot.quantity_of("US0000000002") == D("1")

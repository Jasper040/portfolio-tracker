"""Corporate-action detection and quarantine (design doc Sec 3.4 and Sec 6.3).

`Transactions.csv` renders a split as an ordinary offsetting buy/sell pair. Book it
as a trade and the sale realises a fictitious profit while the position halves --
which is exactly the number M1 has to get right (ORN = 32 shares). Detection
therefore does not trust the trade file alone: it finds the value shape there and
then requires `Account.csv` to name it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from app.ingest.base import NormalisedRow
from app.ingest.corporate_actions import (
    Resolution,
    detect,
    load_resolutions,
    pending,
    suppressed_refs,
)
from app.ingest.degiro.account_csv import parse_account_csv
from app.ingest.degiro.transactions_csv import parse_transactions_csv

GOLDEN_TRANSACTIONS = Path(__file__).parents[1] / "golden" / "degiro_transactions_golden.csv"
GOLDEN_ACCOUNT = Path(__file__).parents[1] / "golden" / "degiro_account_golden.csv"

D = Decimal

SPLIT_KEY = "NL0000000003:2025-01-17:100.00"
PRODUCT_CHANGE_KEY = "US0000000002:2025-01-18:100.00"


def _golden() -> tuple[list[NormalisedRow], list]:
    return parse_transactions_csv(GOLDEN_TRANSACTIONS), parse_account_csv(GOLDEN_ACCOUNT)


def _row(
    ref: str,
    *,
    day: int,
    isin: str,
    quantity: str,
    local: str,
    order_ref: str | None = None,
) -> NormalisedRow:
    return NormalisedRow(
        source="degiro",
        source_ref=ref,
        txn_type="TRADE",
        trade_date=date(2025, 1, day),
        net_base=D(local),
        fee_base=D("0.00"),
        tax_base=D("0.00"),
        raw={},
        isin=isin,
        quantity=D(quantity),
        gross_local=D(local),
        currency_local="EUR",
        order_ref=order_ref,
    )


class TestDetection:
    def test_finds_both_corporate_actions_in_the_golden_export(self) -> None:
        found = detect(*_golden())
        assert [c.key for c in found] == [SPLIT_KEY, PRODUCT_CHANGE_KEY]

    def test_names_the_split_from_the_account_csv_label(self) -> None:
        split = next(c for c in detect(*_golden()) if c.key == SPLIT_KEY)
        assert split.kind == "SPLIT"
        assert split.label.startswith("SPLIT AANPASSING")

    def test_matches_the_product_change_on_the_local_amount_not_the_euro_amount(self) -> None:
        """The trap that makes this join currency-sensitive.

        The product-change pair is USD 100.00 in `Local value` but EUR 90.91 in
        `Value EUR`, and `Account.csv` books it in USD. Joining on the EUR amount
        finds nothing at all -- silently, because "no corporate actions found" is
        also what a clean export looks like.
        """
        change = next(c for c in detect(*_golden()) if c.key == PRODUCT_CHANGE_KEY)
        assert change.kind == "PRODUCT_CHANGE"
        assert change.local_amount == D("100.00")

    def test_covers_exactly_the_two_rows_of_the_pair(self) -> None:
        """The refs are what the importer suppresses, so a wrong one silently
        either books a split as a trade or drops a real trade from the ledger."""
        transactions, account = _golden()
        split = next(c for c in detect(transactions, account) if c.key == SPLIT_KEY)
        covered = {r.source_ref for r in transactions if r.source_ref in split.source_refs}
        assert len(split.source_refs) == 2
        assert {r.isin for r in transactions if r.source_ref in covered} == {"NL0000000003"}

    def test_ignores_ordinary_trades_that_carry_an_order_id(self) -> None:
        """All four blank-Order-ID rows in the real export are corporate-action legs
        and every genuine trade has an id, so the id is the first-pass filter."""
        transactions, account = _golden()
        found = detect(transactions, account)
        with_ids = {r.source_ref for r in transactions if r.order_ref}
        assert with_ids.isdisjoint({ref for c in found for ref in c.source_refs})

    def test_flags_a_value_shaped_pair_that_account_csv_does_not_name(self) -> None:
        """Sec 3.4's secondary check. DeGiro has changed its wording before; an
        unlabelled pair must reach the review queue rather than be waved through."""
        rows = [
            _row("a", day=20, isin="NL0000000009", quantity="-5", local="50.00"),
            _row("b", day=20, isin="NL0000000009", quantity="50", local="-50.00"),
        ]
        found = detect(rows, [])
        assert [c.kind for c in found] == ["UNLABELLED"]
        assert found[0].label == ""

    def test_does_not_flag_a_lone_blank_order_id_row(self) -> None:
        rows = [_row("a", day=20, isin="NL0000000009", quantity="-5", local="50.00")]
        assert detect(rows, []) == ()

    def test_does_not_flag_blank_order_id_rows_that_do_not_offset(self) -> None:
        """Two same-day rows that leave cash behind are not a corporate action."""
        rows = [
            _row("a", day=20, isin="NL0000000009", quantity="-5", local="50.00"),
            _row("b", day=20, isin="NL0000000009", quantity="50", local="-40.00"),
        ]
        assert detect(rows, []) == ()


class TestResolutions:
    YAML = """\
resolutions:
  - key: NL0000000003:2025-01-17:100.00
    treatment: corporate_action
    note: 10-for-1 split
"""

    def _file(self, tmp_path: Path, body: str) -> Path:
        path = tmp_path / "corporate_actions.yaml"
        path.write_text(body, encoding="utf-8")
        return path

    def test_loads_a_resolution_keyed_by_its_event(self, tmp_path: Path) -> None:
        loaded = load_resolutions(self._file(tmp_path, self.YAML))
        assert loaded == {
            SPLIT_KEY: Resolution(
                key=SPLIT_KEY, treatment="corporate_action", note="10-for-1 split"
            )
        }

    def test_a_missing_file_resolves_nothing_rather_than_raising(self, tmp_path: Path) -> None:
        """The file is created by answering the first quarantine, so its absence is
        the normal state of a fresh checkout, not an error."""
        assert load_resolutions(tmp_path / "absent.yaml") == {}

    def test_pending_is_every_candidate_without_a_resolution(self) -> None:
        found = detect(*_golden())
        loaded = {SPLIT_KEY: Resolution(SPLIT_KEY, "corporate_action")}
        assert [c.key for c in pending(found, loaded)] == [PRODUCT_CHANGE_KEY]

    def test_a_resolved_corporate_action_suppresses_both_of_its_rows(self) -> None:
        found = detect(*_golden())
        loaded = {c.key: Resolution(c.key, "corporate_action") for c in found}
        assert len(suppressed_refs(found, loaded)) == 4

    def test_a_resolution_marked_trade_suppresses_nothing(self) -> None:
        """The escape hatch for a false positive: the operator can say the pair was
        a genuine round trip, and the rows stay economic."""
        found = detect(*_golden())
        loaded = {c.key: Resolution(c.key, "trade") for c in found}
        assert suppressed_refs(found, loaded) == frozenset()

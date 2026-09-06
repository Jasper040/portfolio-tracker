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

import pytest

from app.ingest.base import NormalisedRow
from app.ingest.corporate_actions import (
    MalformedResolutions,
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

    def test_does_not_sweep_a_third_row_into_a_pair(self) -> None:
        """The legs are matched to each other, not merely totalled.

        A group that is accepted whenever it sums to zero will absorb any extra
        row that does not change the total -- and if the operator then resolves
        the candidate as a corporate action, that extra row is marked
        non-economic too. A genuine transaction would leave the economic ledger
        with no error and nothing to notice.
        """
        rows = [
            _row("a", day=20, isin="NL0000000009", quantity="-1", local="910.40"),
            _row("b", day=20, isin="NL0000000009", quantity="10", local="-910.40"),
            _row("c", day=20, isin="NL0000000009", quantity="3", local="0.00"),
        ]
        found = detect(rows, [])
        assert len(found) == 1
        assert set(found[0].source_refs) == {"a", "b"}

    def test_pairs_two_events_on_one_day_separately(self) -> None:
        """Four legs, two amounts: two events, not one four-legged one. Sharing a
        key would let one answer resolve an event nobody looked at."""
        rows = [
            _row("a", day=20, isin="NL0000000009", quantity="-1", local="100.00"),
            _row("b", day=20, isin="NL0000000009", quantity="10", local="-100.00"),
            _row("c", day=20, isin="NL0000000009", quantity="-2", local="250.00"),
            _row("d", day=20, isin="NL0000000009", quantity="20", local="-250.00"),
        ]
        found = detect(rows, [])
        assert {c.local_amount for c in found} == {D("100.00"), D("250.00")}
        assert [set(c.source_refs) for c in found] == [{"a", "b"}, {"c", "d"}]


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


class TestMalformedResolutions:
    """A resolutions file that is wrong must raise, not resolve.

    The whole point of the quarantine is that a corporate action cannot pass
    unnoticed. A file this loader accepts but misreads is worse than no file at
    all: the import succeeds, the operator believes the question was answered the
    way they wrote it, and nothing anywhere says otherwise.
    """

    def _load(self, tmp_path: Path, body: str) -> dict[str, Resolution]:
        path = tmp_path / "corporate_actions.yaml"
        path.write_text(body, encoding="utf-8")
        return load_resolutions(path)

    def test_a_missing_treatment_is_rejected_not_assumed(self, tmp_path: Path) -> None:
        """Defaulting it silently turns an unanswered question into 'suppress'."""
        with pytest.raises(MalformedResolutions, match="treatment"):
            self._load(tmp_path, f"resolutions:\n  - key: {SPLIT_KEY}\n")

    def test_a_misspelled_field_is_rejected(self, tmp_path: Path) -> None:
        """The failure this protects against, exactly.

        An operator decides a quarantined pair was a genuine round trip and writes
        `treatement: trade`. Ignoring the unknown field and defaulting the real one
        suppresses two real trade legs -- an import that succeeds and is wrong.
        """
        body = f"resolutions:\n  - key: {SPLIT_KEY}\n    treatement: trade\n"
        with pytest.raises(MalformedResolutions, match="treatement"):
            self._load(tmp_path, body)

    def test_a_duplicate_key_is_rejected(self, tmp_path: Path) -> None:
        """Two answers to one question. Taking the last one silently discards a
        decision the operator made and can still see in the file."""
        body = (
            f"resolutions:\n"
            f"  - key: {SPLIT_KEY}\n    treatment: trade\n"
            f"  - key: {SPLIT_KEY}\n    treatment: corporate_action\n"
        )
        with pytest.raises(MalformedResolutions, match="twice"):
            self._load(tmp_path, body)

    def test_an_empty_key_is_rejected(self, tmp_path: Path) -> None:
        """`key:` with nothing after it parses as None and coerces to "None",
        which matches no event -- an answer that silently does nothing."""
        with pytest.raises(MalformedResolutions, match="key"):
            self._load(tmp_path, "resolutions:\n  - key:\n    treatment: trade\n")

    def test_an_unparseable_file_names_itself(self, tmp_path: Path) -> None:
        """A YAML syntax error must reach the operator as a sentence about their
        file, not as a library traceback out of the import command."""
        with pytest.raises(MalformedResolutions, match="corporate_actions.yaml"):
            self._load(tmp_path, "resolutions: [unclosed\n")

    def test_a_valid_file_still_loads(self, tmp_path: Path) -> None:
        """The guard rejects malformed input without narrowing what is accepted:
        `note` stays optional."""
        body = f"resolutions:\n  - key: {SPLIT_KEY}\n    treatment: trade\n"
        assert self._load(tmp_path, body) == {SPLIT_KEY: Resolution(SPLIT_KEY, "trade", "")}

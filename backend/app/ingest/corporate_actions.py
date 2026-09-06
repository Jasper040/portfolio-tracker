"""Corporate-action detection and quarantine (design doc Sec 3.4 and Sec 6.3).

`Transactions.csv` does not label corporate actions. It renders the ORN 10-for-1
split as an ordinary offsetting buy/sell pair with a blank Order ID, and nothing in
those two rows says they are anything else. Import them as trades and the sale
realises a fictitious profit, the buy opens a lot at the wrong basis, and the
position ends at 1 share instead of 32 -- which is the single number M1 is measured
against.

Detection is therefore a join, not a heuristic. `Account.csv` is the only file that
names these events (`SPLIT AANPASSING`, `PRODUCTWIJZIGING`), so the value shape in
the trade file is matched against that label on `(date, isin, abs(amount))`.

Two details make or break the join:

* **The amount is the LOCAL one.** DeGiro books the Meridian Mining product change as
  USD 845.75 in `Account.csv` and carries USD 845.75 in `Local value`, but EUR
  733.20 in `Value EUR`. Joining on the euro amount matches nothing -- and finding
  nothing is indistinguishable from a clean export, so the failure is silent.
* **A blank Order ID is the value shape.** All four blank-id rows in the real
  export are corporate-action legs; all 108 genuine trades carry an id.

An offsetting pair that `Account.csv` does not name is still reported, as Sec 3.4
requires: DeGiro has changed its wording once already, and that wording is the only
thing this detection rests on.

Pure, except for reading the resolutions file: parsed rows in, candidates out.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml

from app.ingest.base import NormalisedRow
from app.ingest.degiro.account_csv import AccountRow

#: What `Account.csv` called the event.
SPLIT = "SPLIT"
PRODUCT_CHANGE = "PRODUCT_CHANGE"
#: A value-shaped pair `Account.csv` does not name. Not a lesser finding -- it is
#: the case Sec 3.4 keeps the heuristic around for, and it needs a human answer.
UNLABELLED = "UNLABELLED"

_LABEL_PREFIXES: tuple[tuple[str, str], ...] = (
    ("split aanpassing", SPLIT),
    ("productwijziging", PRODUCT_CHANGE),
)

#: How a resolution says a candidate should be treated.
CORPORATE_ACTION = "corporate_action"
TRADE = "trade"
_TREATMENTS = frozenset({CORPORATE_ACTION, TRADE})

_ACCOUNT_TYPE = "CORPORATE_ACTION"


@dataclass(frozen=True, slots=True)
class CorporateAction:
    """One offsetting pair in `Transactions.csv` that is probably not a trade."""

    key: str
    trade_date: date
    isin: str
    local_amount: Decimal
    kind: str
    label: str
    #: The `Transactions.csv` rows this event covers. These are what the importer
    #: marks non-economic, so a wrong ref either books a split as a trade or drops
    #: a genuine trade out of the ledger.
    source_refs: tuple[str, ...]

    @property
    def labelled(self) -> bool:
        return self.kind != UNLABELLED


@dataclass(frozen=True, slots=True)
class Resolution:
    """A human answer to one quarantined candidate, from `corporate_actions.yaml`.

    Stored outside the ledger as a ledger-adjacent fact (Sec 6.3) so `rebuild()`
    reproduces the same answer without asking again.
    """

    key: str
    treatment: str
    note: str = ""


def event_key(isin: str, trade_date: date, local_amount: Decimal) -> str:
    """The stable identity of one corporate action.

    Formatted to two decimals rather than interpolated raw: `Decimal("100.0")` and
    `Decimal("100.00")` are equal numbers with different `str()`, and this key is
    persisted in a hand-edited YAML file -- an unstable rendering would silently
    orphan an answer the operator has already given.
    """
    return f"{isin}:{trade_date.isoformat()}:{local_amount:.2f}"


def _kind_of(description: str) -> str:
    text = description.casefold()
    for prefix, kind in _LABEL_PREFIXES:
        if text.startswith(prefix):
            return kind
    return UNLABELLED


def _labels(account: Iterable[AccountRow]) -> dict[tuple[date, str, Decimal], str]:
    """Index `Account.csv`'s corporate-action rows by what the trade file can see.

    Both legs of an event carry the same absolute amount, so the first one indexed
    wins: they name the same event and either description identifies it.
    """
    labels: dict[tuple[date, str, Decimal], str] = {}
    for row in account:
        if row.action.txn_type != _ACCOUNT_TYPE or not row.isin or row.change is None:
            continue
        labels.setdefault((row.trade_date, row.isin, abs(row.change)), row.description)
    return labels


def detect(
    transactions: Sequence[NormalisedRow], account: Sequence[AccountRow]
) -> tuple[CorporateAction, ...]:
    """Find every offsetting blank-Order-ID pair and name it from `Account.csv`."""
    groups: dict[tuple[date, str], list[NormalisedRow]] = defaultdict(list)
    for row in transactions:
        if row.order_ref or not row.isin or row.gross_local is None or row.quantity is None:
            continue
        groups[(row.trade_date, row.isin)].append(row)

    labels = _labels(account)
    found: list[CorporateAction] = []
    for (trade_date, isin), rows in groups.items():
        values = [row.gross_local for row in rows if row.gross_local is not None]
        # One leg is not a pair, and legs that leave cash behind are a trade: a
        # corporate action moves shares, so its two legs cancel to the cent.
        if len(values) < 2 or sum(values) != 0:
            continue
        amount = sum((value for value in values if value > 0), Decimal("0.00"))
        description = labels.get((trade_date, isin, amount), "")
        found.append(
            CorporateAction(
                key=event_key(isin, trade_date, amount),
                trade_date=trade_date,
                isin=isin,
                local_amount=amount,
                kind=_kind_of(description),
                label=description,
                source_refs=tuple(row.source_ref for row in rows),
            )
        )
    return tuple(sorted(found, key=lambda candidate: (candidate.trade_date, candidate.isin)))


def load_resolutions(path: Path) -> dict[str, Resolution]:
    """Read the operator's answers. A missing file answers nothing.

    Absence is the normal state of a fresh checkout -- the file is written by
    answering the first quarantine -- so it is not an error. A file that exists but
    is malformed is, because a typo there silently un-answers a question the ledger
    depends on.
    """
    if not path.exists():
        return {}

    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(document, dict):
        raise ValueError(f"{path}: expected a mapping at the top level")

    entries = document.get("resolutions") or []
    resolutions: dict[str, Resolution] = {}
    for entry in entries:
        if not isinstance(entry, dict) or "key" not in entry:
            raise ValueError(f"{path}: every resolution needs a 'key'")
        treatment = str(entry.get("treatment", CORPORATE_ACTION))
        if treatment not in _TREATMENTS:
            raise ValueError(f"{path}: treatment {treatment!r} is not one of {sorted(_TREATMENTS)}")
        key = str(entry["key"])
        resolutions[key] = Resolution(key=key, treatment=treatment, note=str(entry.get("note", "")))
    return resolutions


def pending(
    candidates: Sequence[CorporateAction], resolutions: Mapping[str, Resolution]
) -> tuple[CorporateAction, ...]:
    """The candidates still waiting on a human. Import must refuse while any exist."""
    return tuple(candidate for candidate in candidates if candidate.key not in resolutions)


def suppressed_refs(
    candidates: Sequence[CorporateAction], resolutions: Mapping[str, Resolution]
) -> frozenset[str]:
    """Rows a resolution has declared non-economic.

    A candidate answered `trade` contributes nothing: that is the escape hatch for
    a genuine round trip the value shape happened to match.
    """
    return frozenset(
        ref
        for candidate in candidates
        for ref in candidate.source_refs
        if (resolution := resolutions.get(candidate.key)) is not None
        and resolution.treatment == CORPORATE_ACTION
    )

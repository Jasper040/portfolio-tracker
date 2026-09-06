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
from app.ingest.degiro.account_csv import (
    PRODUCT_CHANGE_PREFIX,
    SPLIT_PREFIX,
    AccountRow,
)

#: What `Account.csv` called the event.
SPLIT = "SPLIT"
PRODUCT_CHANGE = "PRODUCT_CHANGE"
#: A value-shaped pair `Account.csv` does not name. Not a lesser finding -- it is
#: the case Sec 3.4 keeps the heuristic around for, and it needs a human answer.
UNLABELLED = "UNLABELLED"

#: Imported rather than restated -- one copy of DeGiro's wording, in the module
#: that classifies it. See the note beside them in `account_csv`.
_LABEL_PREFIXES: tuple[tuple[str, str], ...] = (
    (SPLIT_PREFIX, SPLIT),
    (PRODUCT_CHANGE_PREFIX, PRODUCT_CHANGE),
)

#: How a resolution says a candidate should be treated.
CORPORATE_ACTION = "corporate_action"
TRADE = "trade"
_TREATMENTS = frozenset({CORPORATE_ACTION, TRADE})

_ACCOUNT_TYPE = "CORPORATE_ACTION"


class MalformedResolutions(ValueError):
    """The answers file exists but cannot be trusted. Names the file and the entry."""


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
        for amount, legs in _matched_legs(rows):
            description = labels.get((trade_date, isin, amount), "")
            found.append(
                CorporateAction(
                    key=event_key(isin, trade_date, amount),
                    trade_date=trade_date,
                    isin=isin,
                    local_amount=amount,
                    kind=_kind_of(description),
                    label=description,
                    source_refs=tuple(leg.source_ref for leg in legs),
                )
            )
    return tuple(
        sorted(
            found,
            key=lambda candidate: (candidate.trade_date, candidate.isin, candidate.local_amount),
        )
    )


def _matched_legs(
    rows: Sequence[NormalisedRow],
) -> list[tuple[Decimal, tuple[NormalisedRow, ...]]]:
    """Pair the legs of a group off against each other by amount.

    Matching, not totalling. A group accepted merely because it sums to zero
    absorbs any extra row that does not change the total -- and once the operator
    resolves that candidate as a corporate action, the extra row is marked
    non-economic with it. A genuine transaction would leave the economic ledger
    with no error raised and nothing on the row to notice.

    A corporate action moves shares, so its legs cancel to the cent against each
    other: one credit answers one debit of the same local amount. Anything left
    unmatched is not part of an event and is left alone.

    Two events of different sizes on one day and instrument therefore come back as
    two candidates. Sharing one key would let a single answer resolve an event
    nobody looked at.
    """
    by_amount: dict[Decimal, tuple[list[NormalisedRow], list[NormalisedRow]]] = defaultdict(
        lambda: ([], [])
    )
    for row in rows:
        if row.gross_local is None or row.gross_local == 0:
            continue
        credits, debits = by_amount[abs(row.gross_local)]
        (credits if row.gross_local > 0 else debits).append(row)

    matched: list[tuple[Decimal, tuple[NormalisedRow, ...]]] = []
    for amount, (credits, debits) in by_amount.items():
        pairs = min(len(credits), len(debits))
        if pairs:
            matched.append((amount, (*credits[:pairs], *debits[:pairs])))
    return matched


#: The complete set of fields a resolution may carry. Anything else is a typo, and
#: a typo here is not cosmetic -- see `load_resolutions`.
_RESOLUTION_FIELDS = frozenset({"key", "treatment", "note"})


def load_resolutions(path: Path) -> dict[str, Resolution]:
    """Read the operator's answers. A missing file answers nothing.

    Absence is the normal state of a fresh checkout -- the file is written by
    answering the first quarantine -- so it is not an error. A file that exists but
    is malformed is, and every malformed shape below raises rather than being
    interpreted, because the failure mode of guessing is an import that succeeds
    and is wrong.

    `treatment` is required rather than defaulted, and unknown fields are rejected,
    for one specific reason. An operator who decides a quarantined pair was a
    genuine round trip writes `treatment: trade`. Misspell that field and a loader
    that defaults the missing one reads the entry as `corporate_action`: it
    suppresses two real trade legs, reports success, and nothing anywhere says the
    answer given was not the answer used. A default here cannot be safe, because
    the two possible answers are opposites and both are plausible.
    """
    if not path.exists():
        return {}

    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as broken:
        # Reached through the import command, so it has to read as a sentence
        # about the operator's file rather than as a parser traceback.
        raise MalformedResolutions(f"{path}: not valid YAML -- {broken}") from broken

    if not isinstance(document, dict):
        raise MalformedResolutions(f"{path}: expected a mapping at the top level")

    entries = document.get("resolutions") or []
    resolutions: dict[str, Resolution] = {}
    for position, entry in enumerate(entries, start=1):
        where = f"{path}: resolution {position}"
        if not isinstance(entry, dict):
            raise MalformedResolutions(f"{where} is not a mapping")

        unknown = sorted(set(entry) - _RESOLUTION_FIELDS)
        if unknown:
            raise MalformedResolutions(
                f"{where} has unknown field(s) {unknown}; expected "
                f"{sorted(_RESOLUTION_FIELDS)}"
            )

        key = entry.get("key")
        if not isinstance(key, str) or not key.strip():
            raise MalformedResolutions(f"{where} needs a non-empty string 'key'")
        if key in resolutions:
            raise MalformedResolutions(f"{path}: key {key!r} is answered twice")

        if "treatment" not in entry:
            raise MalformedResolutions(
                f"{where} ({key}) needs a 'treatment' of {sorted(_TREATMENTS)}"
            )
        treatment = entry["treatment"]
        if treatment not in _TREATMENTS:
            raise MalformedResolutions(
                f"{where} ({key}): treatment {treatment!r} is not one of {sorted(_TREATMENTS)}"
            )

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

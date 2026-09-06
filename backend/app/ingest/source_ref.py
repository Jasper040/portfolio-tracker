"""Deterministic dedupe keys.

Two constraints pull against each other:

* Re-importing the same file must change nothing, even if DeGiro re-exports the
  rows in a different order.
* Two partial fills that are byte-identical are two real trades and must survive
  as two rows.

The resolution is an ordinal assigned *within* each group of otherwise-identical
rows, after sorting the whole file by the identity fields. Because the ordinal is
derived from a sorted grouping rather than from file position, shuffling the input
produces the same set of refs, while duplicates still get 0, 1, 2, ...

`order_ref` alone is not a key: 105 distinct order ids cover 112 rows in the real
export, because partial fills share one id and synthetic rows have none.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import astuple, dataclass
from typing import TypeVar


@dataclass(frozen=True, slots=True)
class RefInput:
    order_ref: str
    trade_datetime: str
    isin: str
    quantity: str
    price: str


@dataclass(frozen=True, slots=True)
class AccountRefInput:
    """What identifies a cash-book row.

    Account.csv has no order id on most rows and no quantity or price on any, so
    the description is doing the work an instrument and a size do in a trade: it is
    the only thing separating a dividend from the withholding booked beside it, at
    the same minute, against the same ISIN.

    `kind` is a constant. Both files feed one `UNIQUE(source, source_ref)`
    constraint, and a collision there does not raise -- the importer skips a ref it
    has already seen, so a trade and a cash row that hashed alike would silently
    lose one of the two. Six fields versus five is already enough to keep the
    length-prefixed encodings apart, but relying on that is relying on an argument
    a future field could quietly invalidate.
    """

    kind: str
    trade_datetime: str
    isin: str
    description: str
    change: str
    currency: str


_Ref = TypeVar("_Ref", RefInput, AccountRefInput)


def assign_source_refs(inputs: Sequence[_Ref]) -> list[str]:
    order = sorted(range(len(inputs)), key=lambda i: astuple(inputs[i]))
    seen: dict[_Ref, int] = defaultdict(int)
    refs: list[str] = [""] * len(inputs)
    for i in order:
        row = inputs[i]
        ordinal = seen[row]
        seen[row] += 1
        refs[i] = hashlib.sha256(_payload(row, ordinal).encode("utf-8")).hexdigest()
    return refs


def _payload(row: RefInput | AccountRefInput, ordinal: int) -> str:
    """Length-prefix every field so the encoding is injective.

    A plain "|".join is not. RefInput(order_ref="X|Y", trade_datetime="T", ...) and
    RefInput(order_ref="X", trade_datetime="Y|T", ...) both join to "X|Y|T|...", so
    two distinct trades hash identically and one silently vanishes from the ledger.
    These are raw CSV cell values — nothing upstream guarantees they contain no
    delimiter — and a second source (SnapTrade) will feed this same key later.

    Length prefixes make the encoding unambiguous even when a field itself looks
    like one: "3:xy" encodes as "4:3:xy", which can only be read back one way.

    This format is effectively persistent schema. Changing it changes every ref, so
    an existing ledger would have to be rebuilt rather than re-imported.
    """
    return "".join(f"{len(field)}:{field}" for field in (*astuple(row), str(ordinal)))

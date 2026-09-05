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


@dataclass(frozen=True, slots=True)
class RefInput:
    order_ref: str
    trade_datetime: str
    isin: str
    quantity: str
    price: str


def assign_source_refs(inputs: Sequence[RefInput]) -> list[str]:
    order = sorted(range(len(inputs)), key=lambda i: astuple(inputs[i]))
    seen: dict[RefInput, int] = defaultdict(int)
    refs: list[str] = [""] * len(inputs)
    for i in order:
        row = inputs[i]
        ordinal = seen[row]
        seen[row] += 1
        payload = "|".join([*astuple(row), str(ordinal)])
        refs[i] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return refs

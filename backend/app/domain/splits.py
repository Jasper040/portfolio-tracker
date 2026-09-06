"""Share splits, derived from the ledger rather than declared in a config file.

Design doc Sec 6.3, decided 2026-09-06. M0 already identified each corporate action
and flagged both its legs `is_economic=False`. Those two legs state the ratio: ORN
went out 1 share and came back 10, which is 10:1. Reading it from the export keeps
the ratio broker truth and keeps the answers file to decisions only.

A suppressed pair whose share count does not change is a product change, not a
split. It is skipped: adjusting every lot by a factor of one is a no-op that would
still appear in the audit trail as an event that happened.

Pure: rows in, splits out.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.domain.orders import LedgerRow

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class Split:
    """A change in share count that left cost basis untouched.

    `ratio` is new shares per old share: 10 for a 10-for-1, `0.1` for a 1-for-10
    reverse. A holding of `q` becomes `q * ratio` at a price of `price / ratio`.
    """

    isin: str
    effective_on: date
    ratio: Decimal


def derive_splits(rows: Sequence[LedgerRow]) -> list[Split]:
    """Read every split out of the corporate-action legs M0 suppressed.

    Oldest first: two splits on one instrument compound, and applying them out of
    order gives a different -- wrong -- share count.
    """
    groups: dict[tuple[str, date], list[LedgerRow]] = defaultdict(list)
    for row in rows:
        if row.is_economic or row.isin is None or row.quantity is None or row.quantity == 0:
            continue
        groups[(row.isin, row.trade_date)].append(row)

    splits: list[Split] = []
    for (isin, effective_on), legs in groups.items():
        out = sum((leg.quantity or _ZERO for leg in legs if (leg.quantity or _ZERO) < 0), _ZERO)
        into = sum((leg.quantity or _ZERO for leg in legs if (leg.quantity or _ZERO) > 0), _ZERO)
        if out == 0 or into == 0:
            if out == 0 and into != 0:
                raise ValueError(
                    f"{isin} on {effective_on}: a corporate action with zero shares "
                    "surrendered has no derivable ratio"
                )
            continue
        ratio = into / abs(out)
        # 1.0 is a product change: the instrument changed, the share count did not.
        if ratio == 1:
            continue
        splits.append(Split(isin=isin, effective_on=effective_on, ratio=ratio))

    return sorted(splits, key=lambda split: (split.effective_on, split.isin))

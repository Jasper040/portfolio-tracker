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
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

from app.domain.lots import LotTransaction
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
        # One Decimal division, so a ratio that does not terminate in base 10 -- a
        # 1-for-3 reverse split gives 0.333... -- is exact only to Decimal's default
        # 28-digit context. `quantity * price` is then invariant to 28 digits rather
        # than exactly, which is far tighter than any figure this app displays but
        # is not the exactness the rest of the money path guarantees. Every ratio
        # the design doc commits to testing terminates (2:1, 3:2, 1:10), as does the
        # one real split (ORN 10:1), so nothing exercises it today. Worth knowing
        # before someone meets an odd ratio and wonders where a digit went.
        ratio = into / abs(out)
        # 1.0 is a product change: the instrument changed, the share count did not.
        if ratio == 1:
            continue
        splits.append(Split(isin=isin, effective_on=effective_on, ratio=ratio))

    return sorted(splits, key=lambda split: (split.effective_on, split.isin))


def apply_splits(
    fills: Sequence[LotTransaction], splits: Sequence[Split]
) -> list[LotTransaction]:
    """Restate pre-split fills in post-split shares.

    Sec 7.1 phrases this as adjusting open lots. Doing it to the transactions before
    matching gets the same result and leaves the matcher untouched, because
    `quantity * price` is invariant under the adjustment -- the cost basis cannot
    drift no matter how many splits compound.

    Strictly before, never on the day: DeGiro books the adjustment on the split date
    itself, so a trade that day is already denominated in new shares and scaling it
    would count the split twice.

    Sales are adjusted as well as buys. A pre-split sale of one old share is a sale
    of ten new ones; leaving it alone would consume one of the ten the split just
    created and leave the position nine shares too high.

    `splits` must be for this instrument only, oldest first -- `derive_splits`
    returns them that way and the caller filters by ISIN.
    """
    adjusted = list(fills)
    for split in splits:
        adjusted = [
            replace(
                fill,
                quantity=fill.quantity * split.ratio,
                price=fill.price / split.ratio,
            )
            if fill.trade_date < split.effective_on
            else fill
            for fill in adjusted
        ]
    return adjusted

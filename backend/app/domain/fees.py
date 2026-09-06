"""Fee and tax attribution.

Design doc Sec 6.4. Rows are grouped into economic orders on
`(order_ref, trade_datetime, isin)`, falling back to a synthetic key when
`order_ref` is blank. Fees and trade-linked taxes are attributed **at order level**,
then pro-rata by value across the closures the order produces. Cost basis is
quantity times price, full stop: charges are never capitalised into it. They ride
alongside as their own figure and are deducted from P&L (design doc Sec 6.4,
decided 2026-09-06).

Portfolio-level fees (DeGiro's `Aansluitingskosten`) are never attributed to a lot.
They appear in portfolio cost totals and reduce TWR/MWR, not per-lot P&L.

`Σ attributed fees == Σ ledger fees` is a standing invariant asserted after every
rebuild (Sec 11.2), not only in tests. That is why `apportion` is exact by
construction rather than merely accurate: a split that loses a cent per order would
drift the whole ledger apart over a few thousand rows, and the failure would appear
as an unexplained reconciliation gap long after the cause.

Pure by design (Sec 4.1): no ORM imports, no network, no `datetime.now()`.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal


def apportion(total: Decimal, weights: Sequence[Decimal], places: int = 2) -> list[Decimal]:
    """Split `total` across `weights` so the parts sum to exactly `total`.

    Largest-remainder method: every part is first rounded down to `places`, then
    the leftover units are handed out one at a time to whichever parts were cut
    hardest. That guarantees the sum, which naive per-part rounding does not --
    0.10 split three ways rounds to 0.03 each and loses a cent.

    Rounding down uses ROUND_FLOOR rather than ROUND_DOWN so the leftover is
    non-negative for negative totals too (ROUND_DOWN rounds toward zero, which
    would make the residual negative and hand out units in the wrong direction).
    A credited tax is therefore split by the same code as a charged fee.
    """
    if not weights:
        return []
    if any(w < 0 for w in weights):
        # A negative weight means the caller has passed a sell where a buy was
        # expected. Attributing a negative fee would be plausible and wrong.
        raise ValueError("weights must be non-negative")

    step = Decimal(1).scaleb(-places)

    total_weight = sum(weights, Decimal(0))
    if total_weight == 0:
        # An order whose fills all have zero value still carries a fee, and it has
        # to land somewhere. Splitting equally keeps the invariant; dropping it
        # would break the invariant, and dividing by zero would abort the rebuild.
        shares: Sequence[Decimal] = [Decimal(1)] * len(weights)
        total_weight = Decimal(len(weights))
    else:
        shares = weights

    exact = [total * share / total_weight for share in shares]
    parts = [value.quantize(step, rounding=ROUND_FLOOR) for value in exact]

    residual = total - sum(parts, Decimal(0))
    units = int((residual / step).to_integral_value(rounding=ROUND_HALF_UP))

    # Rank by how much each part lost to rounding, largest shortfall first. Ties
    # keep their original order, so the split is deterministic -- which `rebuild()`
    # depends on (Sec 11.2, double-rebuild produces identical dumps).
    by_remainder = sorted(range(len(parts)), key=lambda i: (-(exact[i] - parts[i]), i))
    for k in range(units):
        parts[by_remainder[k % len(parts)]] += step

    return parts

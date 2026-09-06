"""Does this price series belong to the instrument the ledger traded?

M2 spec section 6. Symbol resolution is NOT a lookup. Run against the real
export, a genuine ISIN lookup returning a genuine multi-year series in the right
currency resolved roughly one instrument in nine to a LEVERAGED OR INVERSE ETF on
the same underlying -- once, to a 2x-short product trading at a fraction of the
underlying's price and moving in the opposite direction. Valued that way the
position is wrong by a factor of tens and the portfolio chart slopes the wrong
way on exactly the days the reader most wants to trust it.

Every one of those wrong matches passed the checks an implementation would
naturally apply: the ISIN resolved, the ticker existed, the series returned years
of daily bars, and the currency matched the ledger. **"A series came back" is not
evidence that it is the right series.**

The account is the oracle. It records what was actually paid, per instrument, per
date, and a price series for the same instrument must agree with that. Three
conditions, none sufficient alone:

1. the series currency matches the ledger's trade currency;
2. every executed trade agrees with the series, after split adjustment, within
   +/-30% (M2-10);
3. the agreement is stable across trades -- a single lucky ratio proves nothing.

The band is deliberately generous. An executed price is an intraday fill and a
close is end of day, so a few percent of honest disagreement is expected on a
volatile day, while the closest wrong answer observed was off by a factor of
nearly three. There is around five times the margin needed, and the asymmetry
justifies erring wide: a false quarantine costs one question, a false accept puts
a badly wrong number on the chart and offers no clue that it is wrong.

Pure: candidate series and ledger trades in, verdicts out. That is what makes the
leveraged-ETF case a unit-testable golden fixture rather than something only
reproducible against the network.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.domain.splits import Split

#: A ratio must lie inside this band (M2-10). Executed price over provider close.
BAND_LOW = Decimal("0.70")
BAND_HIGH = Decimal("1.30")

#: max(ratios) / min(ratios) across an instrument's trades. Condition 3: every
#: ratio can sit inside the band and still drift, which is what a wrong series
#: tracking a correlated underlying looks like.
SPREAD_MAX = Decimal("1.30")

#: How far back a close may be carried to meet a trade. The same four days the
#: valuation join calls fresh: a weekend plus one holiday.
NEAR_DAYS = 4

AGREES = "agrees"
NO_TRADES = "no comparable trade in the ledger"
CURRENCY_MISMATCH = "currency does not match the ledger's trade currency"
NO_CLOSE_NEAR_TRADE = "no close within four days of a trade"
OUT_OF_BAND = "an executed price disagrees with the series by more than 30%"
UNSTABLE = "the agreement is not stable across trades"

_ONE = Decimal("1")


@dataclass(frozen=True, slots=True)
class TradeObservation:
    """One executed fill, as the broker recorded it.

    `price_local` is the TRADE currency price, not the base-currency one M1's lot
    matcher uses. A provider quotes a series in its own currency, so the
    comparison has to happen there or the FX rate becomes a third unknown in a
    check that already has two.

    Not split-adjusted: `assess` applies M1's derived ratio.
    """

    trade_date: date
    price_local: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class CandidateSeries:
    """One provider's answer, reduced to what the check needs.

    `closes` are split-adjusted and dividend-UNadjusted -- the `close_unadjusted`
    column. Comparing against a total-return series would introduce a growing
    divergence that looks exactly like a slowly wrong symbol.
    """

    symbol: str
    currency: str
    closes: Mapping[date, Decimal]


@dataclass(frozen=True, slots=True)
class Verdict:
    symbol: str
    accepted: bool
    reason: str
    #: Every measured ratio, kept even on a rejection: the operator answering the
    #: quarantine needs to see why, and "3.33, 4.80, 43.33" is the sentence that
    #: explains it.
    ratios: tuple[Decimal, ...]

    @property
    def worst_ratio(self) -> Decimal | None:
        """The ratio furthest from parity, in either direction."""
        if not self.ratios:
            return None
        return max(self.ratios, key=lambda r: max(r, _ONE / r) if r else Decimal("Infinity"))


def split_factor(splits: Sequence[Split], on: date) -> Decimal:
    """New shares per old share for a trade executed on `on`.

    The product of every split effective strictly AFTER the trade date -- the
    same strict inequality `apply_splits` uses, because DeGiro books the
    adjustment on the split date itself and a trade that day is already
    denominated in new shares.

    `splits` must already be filtered to one instrument.
    """
    factor = _ONE
    for split in splits:
        if split.effective_on > on:
            factor *= split.ratio
    return factor


def _close_on_or_before(closes: Mapping[date, Decimal], on: date) -> Decimal | None:
    """The last close at most `NEAR_DAYS` before `on`, or None.

    A hole wider than that has not been checked, and unchecked is not agreed.
    """
    for back in range(NEAR_DAYS + 1):
        found = closes.get(on - timedelta(days=back))
        if found is not None and found != 0:
            return found
    return None


def assess(
    candidate: CandidateSeries,
    trades: Sequence[TradeObservation],
    splits: Sequence[Split],
) -> Verdict:
    """Measure one candidate against the ledger. All three conditions, in order."""
    if not trades:
        return Verdict(candidate.symbol, False, NO_TRADES, ())

    currencies = {trade.currency for trade in trades}
    if len(currencies) != 1 or candidate.currency not in currencies:
        return Verdict(candidate.symbol, False, CURRENCY_MISMATCH, ())

    ratios: list[Decimal] = []
    for trade in trades:
        close = _close_on_or_before(candidate.closes, trade.trade_date)
        if close is None:
            return Verdict(candidate.symbol, False, NO_CLOSE_NEAR_TRADE, tuple(ratios))
        adjusted = trade.price_local / split_factor(splits, trade.trade_date)
        ratios.append(adjusted / close)

    measured = tuple(ratios)
    if any(ratio < BAND_LOW or ratio > BAND_HIGH for ratio in measured):
        return Verdict(candidate.symbol, False, OUT_OF_BAND, measured)

    if len(measured) > 1 and max(measured) / min(measured) > SPREAD_MAX:
        return Verdict(candidate.symbol, False, UNSTABLE, measured)

    return Verdict(candidate.symbol, True, AGREES, measured)


def judge(
    candidates: Sequence[CandidateSeries],
    trades: Sequence[TradeObservation],
    splits: Sequence[Split],
) -> tuple[Verdict, ...]:
    """A verdict for every candidate, in the order they were offered.

    All of them, not just the winner: a candidate that vanished silently would
    leave the operator answering a quarantine with no evidence attached.
    """
    return tuple(assess(candidate, trades, splits) for candidate in candidates)


def accepted_symbol(verdicts: Sequence[Verdict]) -> str | None:
    """The one symbol that passed, or None.

    None when nothing passed AND when more than one did. Two listings of the same
    instrument on two venues both agree with the ledger, and picking one
    arbitrarily would silently choose a venue with different liquidity and a
    different close time. Ambiguity is a question, not a tie-break.
    """
    accepted = [verdict.symbol for verdict in verdicts if verdict.accepted]
    return accepted[0] if len(accepted) == 1 else None

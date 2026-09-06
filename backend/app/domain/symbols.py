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

#: max(distance) / min(distance) across candidates that have ALREADY passed
#: `assess`. Not the same measurement as `SPREAD_MAX`: that one asks whether one
#: candidate is stable across several trades, this one asks whether several
#: DIFFERENT candidates -- each already proved stable on its own -- are close
#: enough to each other to be the same instrument quoted on different venues
#: rather than two genuinely different answers. Evidence: of 21 quarantined
#: instruments in a real run, 20 had multiple passing candidates that agreed
#: with each other to within 10%, which is the band this constant encodes.
VENUE_SPREAD = Decimal("1.10")

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


def _distance_from_parity(verdict: Verdict) -> Decimal:
    """How far this candidate's worst trade sits from a ratio of 1, folded so
    that 0.95 and 1.05 -- equally wrong in opposite directions -- compare equal.
    """
    worst = verdict.worst_ratio
    assert worst is not None, "an accepted verdict always has at least one ratio"
    return max(worst, _ONE / worst)


def _ranked_by_distance(accepted: Sequence[Verdict]) -> list[tuple[Decimal, Verdict]]:
    """Accepted verdicts, closest to parity first. Ties break on symbol name --
    never on the order candidates happened to be offered in -- so the winner
    does not depend on which provider answered first."""
    return sorted(
        ((_distance_from_parity(verdict), verdict) for verdict in accepted),
        key=lambda pair: (pair[0], pair[1].symbol),
    )


def _venue_tie_break(verdicts: Sequence[Verdict]) -> list[tuple[Decimal, Verdict]] | None:
    """The ranked, agreeing group of passing candidates, or None.

    None when fewer than two candidates passed (nothing to break a tie between)
    or when the passing candidates disagree with each other by more than
    `VENUE_SPREAD` (a genuine ambiguity, not a multi-venue listing).
    """
    accepted = [verdict for verdict in verdicts if verdict.accepted]
    if len(accepted) < 2:
        return None
    ranked = _ranked_by_distance(accepted)
    closest, furthest = ranked[0][0], ranked[-1][0]
    if furthest / closest > VENUE_SPREAD:
        return None
    return ranked


def accepted_symbol(verdicts: Sequence[Verdict]) -> str | None:
    """The symbol to use, or None when the ambiguity still needs a human.

    The failure this module exists to catch -- a leveraged or inverse product on
    the same underlying -- produces a candidate that DISAGREES WITH THE LEDGER.
    `assess` rejects it outright, on OUT_OF_BAND or UNSTABLE, so an impostor
    never has `accepted=True` and never reaches this function's tie-break at
    all. What reaches here, when more than one candidate passed, is a set of
    candidates that each independently proved themselves against the ledger's
    own executed prices. Choosing among THEM is a tie between equivalent
    answers -- two or more listings of one instrument on different venues --
    not a silent choice between different ones.

    So: zero passing candidates is still None (nothing to offer). One is that
    one, unchanged. Two or more are resolved automatically ONLY if they agree
    with each other -- within `VENUE_SPREAD` of parity-distance, the same
    "several venues, one instrument" pattern the evidence showed 20 times out
    of 21 real quarantines -- and the answer is the one closest to parity,
    ties broken on symbol name. If the passing candidates disagree with each
    other beyond that band, that is a real ambiguity and still returns None: a
    human decides, exactly as before.

    Any auto-resolution here can be overridden by adding the ISIN to
    `config/instrument_symbols.yaml` -- an answer file entry always wins and is
    never probed, let alone tie-broken. See `resolution_note` for the line that
    makes an auto-resolution visible rather than silent.
    """
    accepted = [verdict for verdict in verdicts if verdict.accepted]
    if len(accepted) == 1:
        return accepted[0].symbol

    ranked = _venue_tie_break(verdicts)
    return ranked[0][1].symbol if ranked else None


def resolution_note(verdicts: Sequence[Verdict]) -> str | None:
    """A one-line explanation when `accepted_symbol` broke a tie, else None.

    This project's ethic is that a number that depends on a methodological
    choice carries that choice visibly next to it. Auto-resolving several
    agreeing venues to one symbol is such a choice, so the caller that prints
    or stores the resolved symbol should also be able to show -- and the
    operator should be able to override, in `config/instrument_symbols.yaml`
    -- which alternatives were on the table and why one was preferred.

    None both when nothing passed, when exactly one candidate passed (no tie
    to break), and when the passing candidates disagreed and `accepted_symbol`
    returned None -- there is no resolution to explain in either case.
    """
    ranked = _venue_tie_break(verdicts)
    if ranked is None:
        return None
    winner = ranked[0][1]
    alternatives = ", ".join(verdict.symbol for _, verdict in ranked[1:])
    return (
        f"{winner.symbol} chosen over {alternatives} -- agreeing venues within "
        f"{VENUE_SPREAD} of each other, {winner.symbol} closest to parity"
    )

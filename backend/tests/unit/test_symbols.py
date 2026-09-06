"""Whether a price series belongs to the instrument the ledger traded.

The failure this exists to catch is not a near miss. In the provider spike, one
large holding resolved -- via a genuine ISIN lookup, to a real ticker, returning
years of real daily bars in the right currency -- to a 2x-SHORT product on the
same underlying, trading at a fraction of the price and moving the opposite way.
Valued that way the position is wrong by a factor of tens, and the portfolio
chart slopes the wrong way on exactly the days the reader most wants to trust it.

So "a series came back" is not evidence. The account is its own oracle: it
records what was actually paid, per instrument, per date. `TestTheImpostor`
below is that case as a golden fixture, with invented numbers in the shape the
spike measured -- a discriminator that cannot be shown to reject it is
decoration.

One wrinkle the tests pin down. A provider's series is split-adjusted and an
executed price is not, so a pre-split trade compares at the split ratio rather
than at parity. M1 already derives that ratio from the corporate-action legs M0
suppressed, so `TestSplitAdjustment` shows the same trade failing at 10.0 without
the correction and passing at 1.0 with it -- the check independently
rediscovering a split M1 derived by a completely different route.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.domain.splits import Split
from app.domain.symbols import (
    AGREES,
    CURRENCY_MISMATCH,
    NO_CLOSE_NEAR_TRADE,
    NO_TRADES,
    OUT_OF_BAND,
    UNSTABLE,
    CandidateSeries,
    TradeObservation,
    accepted_symbol,
    assess,
    judge,
    resolution_note,
    split_factor,
)

D = Decimal


def buy(on: date, price: str, currency: str = "EUR") -> TradeObservation:
    return TradeObservation(trade_date=on, price_local=D(price), currency=currency)


def series(symbol: str, closes: dict[date, str], currency: str = "EUR") -> CandidateSeries:
    return CandidateSeries(
        symbol=symbol,
        currency=currency,
        closes={on: D(value) for on, value in closes.items()},
    )


class TestSplitFactor:
    def test_is_one_when_no_split_follows_the_trade(self) -> None:
        splits = [Split("NL0000000001", date(2025, 1, 8), D("10"))]
        assert split_factor(splits, date(2025, 2, 1)) == D("1")

    def test_is_the_ratio_when_a_split_follows(self) -> None:
        splits = [Split("NL0000000001", date(2025, 1, 8), D("10"))]
        assert split_factor(splits, date(2025, 1, 6)) == D("10")

    def test_a_split_on_the_trade_date_itself_does_not_count(self) -> None:
        """`apply_splits` uses the same strict inequality: DeGiro books the
        adjustment on the split date, so a trade that day is already in new
        shares and correcting it would count the split twice."""
        splits = [Split("NL0000000001", date(2025, 1, 8), D("10"))]
        assert split_factor(splits, date(2025, 1, 8)) == D("1")

    def test_two_splits_compound(self) -> None:
        splits = [
            Split("NL0000000001", date(2025, 1, 8), D("10")),
            Split("NL0000000001", date(2025, 6, 2), D("2")),
        ]
        assert split_factor(splits, date(2025, 1, 6)) == D("20")


class TestAgreement:
    def test_accepts_a_series_that_matches_every_trade(self) -> None:
        verdict = assess(
            series("EXA.AS", {date(2025, 1, 6): "20.00", date(2025, 2, 3): "24.00"}),
            [buy(date(2025, 1, 6), "20.00"), buy(date(2025, 2, 3), "24.00")],
            [],
        )
        assert verdict.accepted
        assert verdict.reason == AGREES

    def test_tolerates_the_gap_between_an_intraday_fill_and_a_close(self) -> None:
        """An executed price is an intraday fill and a close is end of day, so a
        few percent of honest disagreement is expected on a volatile day. The
        band is +/-30% because the closest wrong answer observed was off by a
        factor of nearly three -- around five times the margin needed, and the
        asymmetry justifies erring wide."""
        verdict = assess(
            series("EXA.AS", {date(2025, 1, 6): "20.00"}),
            [buy(date(2025, 1, 6), "21.20")],  # ratio 1.06
            [],
        )
        assert verdict.accepted

    def test_uses_the_latest_close_on_or_before_the_trade(self) -> None:
        """A venue holiday must not fail an otherwise correct symbol. Carrying
        the last close forward is the same rule the valuation join uses."""
        verdict = assess(
            series("EXA.AS", {date(2025, 1, 3): "20.00"}),
            [buy(date(2025, 1, 6), "20.20")],
            [],
        )
        assert verdict.accepted

    def test_rejects_when_no_close_sits_near_a_trade(self) -> None:
        """A series that starts after the trade, or has a week-long hole around
        it, has not been checked -- and unchecked is not the same as agreed."""
        verdict = assess(
            series("EXA.AS", {date(2024, 11, 1): "20.00"}),
            [buy(date(2025, 1, 6), "20.00")],
            [],
        )
        assert not verdict.accepted
        assert verdict.reason == NO_CLOSE_NEAR_TRADE


class TestTheImpostor:
    """The golden fixture: a leveraged or inverse product on the same underlying.

    Shape taken from the spike, numbers invented. The ISIN resolved, the ticker
    existed, the series returned years of daily bars, and the currency matched
    the ledger. The only thing that disagreed was the account.
    """

    def test_rejects_a_leveraged_product_trading_at_a_fraction_of_the_price(self) -> None:
        verdict = assess(
            series(
                "EXA2S.DE",
                {date(2025, 1, 6): "6.00", date(2025, 2, 3): "5.00", date(2025, 3, 3): "0.60"},
            ),
            [
                buy(date(2025, 1, 6), "20.00"),  # ratio 3.33
                buy(date(2025, 2, 3), "24.00"),  # ratio 4.80
                buy(date(2025, 3, 3), "26.00"),  # ratio 43.33
            ],
            [],
        )
        assert not verdict.accepted
        assert verdict.reason == OUT_OF_BAND
        # The ratios are kept on the verdict, not just a yes/no: the operator
        # answering the quarantine needs to see WHY it was rejected, and "3.33,
        # 4.80, 43.33 with no stable value" is the sentence that explains it.
        assert verdict.worst_ratio is not None
        assert verdict.worst_ratio > D("40")

    def test_rejects_an_inverse_product_that_moves_the_other_way(self) -> None:
        """The underlying rose 30% over these three trades; the inverse product
        fell. Each individual ratio drifts, and no band that admits an honest
        intraday gap can also admit this."""
        verdict = assess(
            series(
                "EXA1S.DE",
                {date(2025, 1, 6): "20.00", date(2025, 2, 3): "16.00", date(2025, 3, 3): "13.00"},
            ),
            [
                buy(date(2025, 1, 6), "20.00"),  # ratio 1.00
                buy(date(2025, 2, 3), "24.00"),  # ratio 1.50
                buy(date(2025, 3, 3), "26.00"),  # ratio 2.00
            ],
            [],
        )
        assert not verdict.accepted
        assert verdict.reason == OUT_OF_BAND

    def test_rejects_a_series_that_agrees_once_and_then_drifts(self) -> None:
        """Every ratio inside the band, and still wrong: 0.75 then 1.02 is a 36%
        spread across two trades of one instrument. A single lucky ratio proves
        nothing, which is why stability is a condition of its own."""
        verdict = assess(
            series("EXA.L", {date(2025, 1, 6): "20.00", date(2025, 2, 3): "24.00"}),
            [buy(date(2025, 1, 6), "15.00"), buy(date(2025, 2, 3), "24.48")],
            [],
        )
        assert not verdict.accepted
        assert verdict.reason == UNSTABLE

    def test_offered_alongside_the_correct_symbol_the_impostor_still_never_wins(self) -> None:
        """`accepted_symbol` now auto-resolves when several candidates pass, so
        this closes the door that opens: the impostor must fail `assess` on its
        own merits, before the tie-break logic ever sees it, or "more lenient
        tie-break" would quietly become "more lenient toward impostors"."""
        trades = [
            buy(date(2025, 1, 6), "20.00"),
            buy(date(2025, 2, 3), "24.00"),
            buy(date(2025, 3, 3), "26.00"),
        ]
        impostor = series(
            "EXA2S.DE",
            {date(2025, 1, 6): "6.00", date(2025, 2, 3): "5.00", date(2025, 3, 3): "0.60"},
        )
        correct = series(
            "EXA.AS",
            {date(2025, 1, 6): "20.00", date(2025, 2, 3): "24.00", date(2025, 3, 3): "26.00"},
        )
        verdicts = judge([impostor, correct], trades, [])
        assert accepted_symbol(verdicts) == "EXA.AS"


class TestCurrency:
    def test_rejects_a_series_quoted_in_another_currency(self) -> None:
        verdict = assess(
            series("EXA", {date(2025, 1, 6): "20.00"}, currency="USD"),
            [buy(date(2025, 1, 6), "20.00", currency="EUR")],
            [],
        )
        assert not verdict.accepted
        assert verdict.reason == CURRENCY_MISMATCH

    def test_currency_agreement_alone_does_not_accept(self) -> None:
        """Necessary, and not sufficient: every impostor in the spike was
        denominated in the same currency as its target."""
        verdict = assess(
            series("EXA2S.DE", {date(2025, 1, 6): "6.00"}, currency="EUR"),
            [buy(date(2025, 1, 6), "20.00", currency="EUR")],
            [],
        )
        assert not verdict.accepted

    def test_rejects_when_the_ledger_itself_traded_two_currencies(self) -> None:
        """Not a provider problem: an instrument the ledger priced in two
        currencies has no single trade currency to match against, so no series
        can be proved right and a human has to look."""
        verdict = assess(
            series("EXA", {date(2025, 1, 6): "20.00", date(2025, 2, 3): "24.00"}),
            [buy(date(2025, 1, 6), "20.00", "EUR"), buy(date(2025, 2, 3), "24.00", "USD")],
            [],
        )
        assert not verdict.accepted
        assert verdict.reason == CURRENCY_MISMATCH


class TestTheSeriesCameBackTrap:
    def test_refuses_to_accept_a_series_with_nothing_to_check_it_against(self) -> None:
        """M2-2: never auto-accept on "a series came back". With no comparable
        trade, "every trade agrees" is vacuously true -- which is exactly the
        reasoning that put a 2x-short product on the chart."""
        verdict = assess(series("EXA.AS", {date(2025, 1, 6): "20.00"}), [], [])
        assert not verdict.accepted
        assert verdict.reason == NO_TRADES

    def test_refuses_a_series_with_no_closes_at_all(self) -> None:
        verdict = assess(series("EXA.AS", {}), [buy(date(2025, 1, 6), "20.00")], [])
        assert not verdict.accepted


class TestSplitAdjustment:
    """The check rediscovers, by a completely different route, a split M1 derived
    from the corporate-action legs M0 suppressed."""

    SPLITS = [Split("NL0000000001", date(2025, 1, 8), D("10"))]
    # Bought 1 share at 200.00 before a 10-for-1; the provider's split-adjusted
    # close for that day is 20.00.
    TRADES = [buy(date(2025, 1, 6), "200.00"), buy(date(2025, 2, 3), "24.00")]
    SERIES = series("EXA.AS", {date(2025, 1, 6): "20.00", date(2025, 2, 3): "24.00"})

    def test_the_correct_symbol_agrees_across_the_split(self) -> None:
        verdict = assess(self.SERIES, self.TRADES, self.SPLITS)
        assert verdict.accepted
        assert verdict.ratios == (D("1"), D("1"))

    def test_and_would_be_rejected_at_ten_to_one_without_the_correction(self) -> None:
        """The control. Without M1's ratio the pre-split trade reads as a
        tenfold disagreement, and the right symbol gets quarantined."""
        verdict = assess(self.SERIES, self.TRADES, [])
        assert not verdict.accepted
        assert verdict.ratios[0] == D("10")


class TestChoosingBetweenCandidates:
    TRADES = [buy(date(2025, 1, 6), "20.00"), buy(date(2025, 2, 3), "24.00")]
    RIGHT = series("EXA.AS", {date(2025, 1, 6): "20.00", date(2025, 2, 3): "24.00"})
    WRONG = series("EXA2S.DE", {date(2025, 1, 6): "6.00", date(2025, 2, 3): "5.00"})

    def test_picks_the_one_candidate_that_agrees(self) -> None:
        verdicts = judge([self.WRONG, self.RIGHT], self.TRADES, [])
        assert accepted_symbol(verdicts) == "EXA.AS"

    def test_reports_a_verdict_for_every_candidate_in_the_order_given(self) -> None:
        """The quarantine shows the operator what was tried and what each
        measured. A rejected candidate that vanished silently would leave them
        answering a question with no evidence attached."""
        verdicts = judge([self.WRONG, self.RIGHT], self.TRADES, [])
        assert [v.symbol for v in verdicts] == ["EXA2S.DE", "EXA.AS"]

    def test_two_agreeing_venues_resolve_to_the_one_closest_to_parity(self) -> None:
        """Two listings of the same instrument on two venues both pass, and
        agree with each other -- the review's own finding, 20 times out of 21
        real quarantines. That is no longer a silent arbitrary pick: it is
        `VENUE_SPREAD` recognising one instrument on several venues, and it
        resolves to the venue that sits closest to the ledger's own prices."""
        trades = [buy(date(2025, 1, 6), "20.00")]
        nearer = series("EXA.AS", {date(2025, 1, 6): "20.00"})  # ratio 1.00, distance 1.00
        farther = series("EXA.PA", {date(2025, 1, 6): "21.00"})  # ratio 0.952, distance 1.05
        verdicts = judge([farther, nearer], trades, [])
        assert accepted_symbol(verdicts) == "EXA.AS"

    def test_two_candidates_that_disagree_with_each_other_are_still_none(self) -> None:
        """Each individually sits inside the +/-30% band against the ledger --
        so each passes `assess` on its own -- but 1.00 and 0.80 are 25% apart
        from each other, past `VENUE_SPREAD`. That is a real ambiguity, not two
        venues of one instrument, and still needs a human."""
        trades = [buy(date(2025, 1, 6), "20.00")]
        one = series("EXA.AS", {date(2025, 1, 6): "20.00"})  # ratio 1.00, distance 1.00
        other = series("EXA.PA", {date(2025, 1, 6): "25.00"})  # ratio 0.80, distance 1.25
        verdicts = judge([one, other], trades, [])
        assert accepted_symbol(verdicts) is None

    def test_three_candidates_two_close_and_one_far_are_still_none(self) -> None:
        """A genuine disagreement anywhere in the passing set blocks the whole
        tie-break, even when a majority of the candidates agree with each
        other -- `VENUE_SPREAD` is measured across ALL passing candidates
        (closest to furthest), not just between adjacent ones."""
        trades = [buy(date(2025, 1, 6), "20.00")]
        one = series("EXA.AS", {date(2025, 1, 6): "20.00"})  # ratio 1.00, distance 1.00
        close = series("EXA.PA", {date(2025, 1, 6): "20.20"})  # ratio 0.99, distance 1.01
        far = series("EXA.L", {date(2025, 1, 6): "25.00"})  # ratio 0.80, distance 1.25
        verdicts = judge([one, close, far], trades, [])
        assert accepted_symbol(verdicts) is None

    def test_an_exact_tie_resolves_deterministically_by_symbol_name(self) -> None:
        """Two candidates equally close to parity have nothing but the symbol
        name to break the tie on -- never list position, or the same two
        candidates offered in a different order would silently answer
        differently."""
        twin = series("EXA.PA", {date(2025, 1, 6): "20.00", date(2025, 2, 3): "24.00"})
        assert accepted_symbol(judge([self.RIGHT, twin], self.TRADES, [])) == "EXA.AS"
        # Reversed input order: the same answer, not "whichever came first".
        assert accepted_symbol(judge([twin, self.RIGHT], self.TRADES, [])) == "EXA.AS"

    def test_resolution_note_names_the_winner_and_the_alternatives(self) -> None:
        twin = series("EXA.PA", {date(2025, 1, 6): "20.00", date(2025, 2, 3): "24.00"})
        note = resolution_note(judge([twin, self.RIGHT], self.TRADES, []))
        assert note is not None
        assert "EXA.AS" in note
        assert "EXA.PA" in note

    def test_resolution_note_is_none_when_exactly_one_candidate_passed(self) -> None:
        verdicts = judge([self.WRONG, self.RIGHT], self.TRADES, [])
        assert resolution_note(verdicts) is None

    def test_refuses_when_none_agree(self) -> None:
        assert accepted_symbol(judge([self.WRONG], self.TRADES, [])) is None

    def test_refuses_when_there_are_no_candidates(self) -> None:
        assert accepted_symbol(judge([], self.TRADES, [])) is None

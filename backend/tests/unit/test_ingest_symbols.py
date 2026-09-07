"""Turning a ledger into symbols, and everything that refuses to be turned.

The auto-accept path is narrow on purpose (M2-2). An answer file entry wins
outright, because a human said so. Everything else has to clear
`domain/symbols.py` -- and the instruments that do not become questions rather
than guesses, because a false quarantine costs one question and a false accept
puts a badly wrong number on the chart with no clue that it is wrong.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.ingest.symbols import (
    MANUAL_ANSWER,
    MalformedSymbolAnswers,
    SymbolAnswer,
    instrument_names,
    load_symbol_answers,
    observations,
    resolve_symbols,
)
from app.providers.base import PricePoint, PriceSeries, SymbolCandidate

D = Decimal
ZERO = D("0.00")


@dataclass(frozen=True, slots=True)
class Row:
    source_ref: str
    trade_date: date
    isin: str | None = "NL0000000001"
    product_name: str | None = "Example Holdings"
    trade_time: str | None = "10:00"
    txn_type: str = "BUY"
    quantity: Decimal | None = D("10")
    price_local: Decimal | None = D("20.00")
    currency_local: str | None = "EUR"
    value_base: Decimal | None = D("-200.00")
    net_base: Decimal = D("-200.00")
    fee_base: Decimal = ZERO
    autofx_fee_base: Decimal | None = ZERO
    tax_base: Decimal = ZERO
    order_ref: str | None = "ord-1"
    is_economic: bool = True


class StubResolver:
    name = "stub"

    def __init__(self, by_isin: dict[str, tuple[str, ...]]) -> None:
        self._by_isin = by_isin
        self.probed: list[str] = []
        self.batches = 0

    def candidates(self, isin: str) -> tuple[SymbolCandidate, ...]:
        return self.candidates_for([isin])[isin]

    def candidates_for(self, isins: Sequence[str]) -> dict[str, tuple[SymbolCandidate, ...]]:
        self.batches += 1
        self.probed.extend(isins)
        return {
            isin: tuple(
                SymbolCandidate(symbol=s, name="Example", exchange_code="NA", source="stub")
                for s in self._by_isin.get(isin, ())
            )
            for isin in isins
        }


class StubPrices:
    name = "stub"

    def __init__(self, by_symbol: dict[str, PriceSeries]) -> None:
        self._by_symbol = by_symbol
        self.asked: list[str] = []

    def full_series(self, symbol: str) -> PriceSeries | None:
        self.asked.append(symbol)
        return self._by_symbol.get(symbol)

    def series_since(self, symbol: str, since: date) -> PriceSeries | None:
        return self.full_series(symbol)


def series(symbol: str, closes: dict[date, str], currency: str = "EUR") -> PriceSeries:
    return PriceSeries(
        symbol=symbol,
        currency=currency,
        source="stub",
        points=tuple(
            PricePoint(on=on, close_unadjusted=D(v), close_adjusted=D(v))
            for on, v in sorted(closes.items())
        ),
    )


class TestAnswersFile:
    def test_a_missing_file_answers_nothing(self, tmp_path: Path) -> None:
        assert load_symbol_answers(tmp_path / "absent.yaml") == {}

    def test_reads_a_symbol_answer(self, tmp_path: Path) -> None:
        path = tmp_path / "instrument_symbols.yaml"
        path.write_text(
            "symbols:\n  - isin: NL0000000001\n    symbol: EXA.AS\n    note: checked\n",
            encoding="utf-8",
        )
        answers = load_symbol_answers(path)
        assert answers["NL0000000001"].symbol == "EXA.AS"
        assert answers["NL0000000001"].note == "checked"

    def test_manual_routes_to_the_price_file_rather_than_a_provider(self, tmp_path: Path) -> None:
        """M2 spec section 6.3: an instrument may be answered with a symbol or
        with `manual`. `None` is what the chain reads as "go straight to the
        file"."""
        path = tmp_path / "instrument_symbols.yaml"
        path.write_text(
            f"symbols:\n  - isin: US0000000404\n    symbol: {MANUAL_ANSWER}\n", encoding="utf-8"
        )
        assert load_symbol_answers(path)["US0000000404"].symbol is None

    @pytest.mark.parametrize(
        ("body", "fragment"),
        [
            ("symbols:\n  - symbol: EXA.AS\n", "isin"),
            ("symbols:\n  - isin: NL0000000001\n", "symbol"),
            ("symbols:\n  - isin: NL0000000001\n    ticker: EXA.AS\n", "unknown"),
            ("symbols:\n  - isin: NL0000000001\n    symbol: ''\n", "symbol"),
            ("not-a-mapping\n", "mapping"),
        ],
    )
    def test_refuses_a_shape_it_would_have_to_guess_at(
        self, tmp_path: Path, body: str, fragment: str
    ) -> None:
        """Same strictness as `load_resolutions`, for a sharper reason: a
        mistyped field here silently prices an instrument from the wrong series,
        and nothing downstream can tell."""
        path = tmp_path / "instrument_symbols.yaml"
        path.write_text(body, encoding="utf-8")
        with pytest.raises(MalformedSymbolAnswers, match=fragment):
            load_symbol_answers(path)

    def test_refuses_the_same_instrument_answered_twice(self, tmp_path: Path) -> None:
        path = tmp_path / "instrument_symbols.yaml"
        path.write_text(
            "symbols:\n"
            "  - isin: NL0000000001\n    symbol: EXA.AS\n"
            "  - isin: NL0000000001\n    symbol: EXA.DE\n",
            encoding="utf-8",
        )
        with pytest.raises(MalformedSymbolAnswers, match="twice"):
            load_symbol_answers(path)


class TestObservations:
    def test_takes_the_trade_currency_price_not_the_base_currency_one(self) -> None:
        """A provider quotes in its own currency. Comparing against a EUR price
        would make the FX rate a third unknown in a check that has two."""
        found = observations([Row("a", date(2025, 1, 6))])
        assert found["NL0000000001"][0].price_local == D("20.00")
        assert found["NL0000000001"][0].currency == "EUR"

    def test_skips_rows_that_state_no_local_price(self) -> None:
        found = observations([Row("a", date(2025, 1, 6), price_local=None)])
        assert found == {}

    def test_skips_suppressed_corporate_action_legs(self) -> None:
        """A split leg is not an executed price. Checking a series against one
        would compare a synthetic number to a real close."""
        found = observations([Row("a", date(2025, 1, 6), is_economic=False)])
        assert found == {}

    def test_includes_sells_as_well_as_buys(self) -> None:
        """A sale is an executed price too, and an instrument bought before the
        export window has nothing else to check against."""
        found = observations(
            [Row("a", date(2025, 1, 6), quantity=D("-10"), price_local=D("24.00"))]
        )
        assert found["NL0000000001"][0].price_local == D("24.00")

    def test_orders_observations_oldest_first(self) -> None:
        found = observations(
            [Row("b", date(2025, 2, 3)), Row("a", date(2025, 1, 6))]
        )
        assert [o.trade_date for o in found["NL0000000001"]] == [
            date(2025, 1, 6),
            date(2025, 2, 3),
        ]

    def test_names_each_instrument_from_the_ledger(self) -> None:
        """`models.ledger.Instrument` is declared but nothing populates it, so
        the product name comes from the transaction that carried it."""
        assert instrument_names([Row("a", date(2025, 1, 6))]) == {
            "NL0000000001": "Example Holdings"
        }


class TestResolution:
    TRADES = [Row("a", date(2025, 1, 6)), Row("b", date(2025, 2, 3), price_local=D("24.00"))]
    RIGHT = series("EXA.AS", {date(2025, 1, 6): "20.00", date(2025, 2, 3): "24.00"})
    WRONG = series("EXA2S.DE", {date(2025, 1, 6): "6.00", date(2025, 2, 3): "5.00"})

    def test_accepts_the_candidate_the_ledger_agrees_with(self) -> None:
        report = resolve_symbols(
            self.TRADES,
            answers={},
            resolver=StubResolver({"NL0000000001": ("EXA2S.DE", "EXA.AS")}),
            prices=StubPrices({"EXA.AS": self.RIGHT, "EXA2S.DE": self.WRONG}),
        )
        assert report.resolved == {"NL0000000001": "EXA.AS"}
        assert report.pending == ()

    def test_quarantines_when_only_an_impostor_came_back(self) -> None:
        report = resolve_symbols(
            self.TRADES,
            answers={},
            resolver=StubResolver({"NL0000000001": ("EXA2S.DE",)}),
            prices=StubPrices({"EXA2S.DE": self.WRONG}),
        )
        assert report.resolved == {}
        assert [p.isin for p in report.pending] == ["NL0000000001"]

    def test_the_quarantine_carries_the_measured_ratios(self) -> None:
        """The operator is being asked to choose. A row saying only "could not
        resolve" gives them nothing to choose with."""
        report = resolve_symbols(
            self.TRADES,
            answers={},
            resolver=StubResolver({"NL0000000001": ("EXA2S.DE",)}),
            prices=StubPrices({"EXA2S.DE": self.WRONG}),
        )
        verdict = report.pending[0].verdicts[0]
        assert verdict.symbol == "EXA2S.DE"
        assert verdict.ratios != ()
        assert not verdict.accepted

    def test_quarantines_an_instrument_with_no_candidates_at_all(self) -> None:
        """The resolver offered nothing: `probed` and `verdicts` are both
        empty. That is the "go find an identifier, or route to manual" case,
        distinct from a candidate that was offered but had no price series
        (M2 spec section 6)."""
        report = resolve_symbols(
            self.TRADES,
            answers={},
            resolver=StubResolver({}),
            prices=StubPrices({}),
        )
        assert [p.isin for p in report.pending] == ["NL0000000001"]
        assert report.pending[0].probed == ()
        assert report.pending[0].verdicts == ()

    def test_an_answered_instrument_is_never_probed(self) -> None:
        """A human already decided. Asking a provider anyway would spend a call
        to second-guess them, and would quarantine their answer if the network
        happened to disagree."""
        prices = StubPrices({})
        resolver = StubResolver({"NL0000000001": ("EXA.AS",)})
        report = resolve_symbols(
            self.TRADES,
            answers={"NL0000000001": _answer("NL0000000001", "EXA.AS")},
            resolver=resolver,
            prices=prices,
        )
        assert report.resolved == {"NL0000000001": "EXA.AS"}
        assert prices.asked == []
        assert resolver.probed == []

    def test_resolves_every_unanswered_instrument_in_one_batch_call(self) -> None:
        """The fix this suite exists to pin: `resolve_symbols` gathers every
        unanswered ISIN and asks for all of them in a single `candidates_for`
        call, rather than one `candidates` call per instrument."""
        rows = [
            *self.TRADES,
            Row(
                "c",
                date(2025, 1, 6),
                isin="US0000000404",
                product_name="Other Holdings",
            ),
        ]
        other = series("OTH", {date(2025, 1, 6): "20.00"})
        resolver = StubResolver(
            {"NL0000000001": ("EXA.AS",), "US0000000404": ("OTH",)}
        )
        prices = StubPrices({"EXA.AS": self.RIGHT, "OTH": other})

        report = resolve_symbols(rows, answers={}, resolver=resolver, prices=prices)

        assert resolver.batches == 1
        assert set(resolver.probed) == {"NL0000000001", "US0000000404"}
        assert report.resolved == {"NL0000000001": "EXA.AS", "US0000000404": "OTH"}

    def test_an_instrument_answered_manual_resolves_to_none(self) -> None:
        report = resolve_symbols(
            self.TRADES,
            answers={"NL0000000001": _answer("NL0000000001", None)},
            resolver=StubResolver({}),
            prices=StubPrices({}),
        )
        assert report.resolved == {"NL0000000001": None}
        assert report.pending == ()

    def test_a_candidate_whose_series_came_back_empty_is_dropped_not_judged(self) -> None:
        """Judging it instead would report NO_CLOSE_NEAR_TRADE, which reads as
        "the wrong instrument" when the truth is "nothing came back at all" --
        and the operator would be sent hunting for a ticker that does not
        exist.

        `probed` still names the candidate the resolver offered, even though
        it never got judged: that is what lets the caller tell "the resolver
        found nothing" apart from "a ticker was found but Yahoo has no price
        series for it" (M2 spec section 6) -- the ticker was offered, so
        `probed` is not empty, but nothing could be judged, so `verdicts` is.
        """
        prices = StubPrices({})
        report = resolve_symbols(
            self.TRADES,
            answers={},
            resolver=StubResolver({"NL0000000001": ("EXA.AS",)}),
            prices=prices,
        )
        assert prices.asked == ["EXA.AS"]
        assert [p.isin for p in report.pending] == ["NL0000000001"]
        assert report.pending[0].probed == ("EXA.AS",)
        assert report.pending[0].verdicts == ()

    def test_a_candidate_with_a_series_but_no_points_is_dropped_too(self) -> None:
        empty = PriceSeries(symbol="EXA.AS", currency="EUR", source="stub", points=())
        prices = StubPrices({"EXA.AS": empty})
        report = resolve_symbols(
            self.TRADES,
            answers={},
            resolver=StubResolver({"NL0000000001": ("EXA.AS",)}),
            prices=prices,
        )
        assert prices.asked == ["EXA.AS"]
        assert [p.isin for p in report.pending] == ["NL0000000001"]
        assert report.pending[0].probed == ("EXA.AS",)
        assert report.pending[0].verdicts == ()

    def test_carries_the_split_ratio_into_the_check(self) -> None:
        """The pre-split trade only agrees once M1's derived ratio is applied.
        Without it the right symbol would be quarantined on every instrument
        that ever split."""
        rows = [
            Row("a", date(2025, 1, 6), price_local=D("200.00")),
            Row("b", date(2025, 2, 3), price_local=D("24.00")),
            Row(
                "split-out",
                date(2025, 1, 8),
                quantity=D("-1"),
                price_local=D("200.00"),
                order_ref=None,
                is_economic=False,
            ),
            Row(
                "split-in",
                date(2025, 1, 8),
                quantity=D("10"),
                price_local=D("20.00"),
                order_ref=None,
                is_economic=False,
            ),
        ]
        report = resolve_symbols(
            rows,
            answers={},
            resolver=StubResolver({"NL0000000001": ("EXA.AS",)}),
            prices=StubPrices({"EXA.AS": self.RIGHT}),
        )
        assert report.resolved == {"NL0000000001": "EXA.AS"}


def _answer(isin: str, symbol: str | None) -> SymbolAnswer:
    return SymbolAnswer(isin=isin, symbol=symbol, note="")

"""Trying providers in order, and recording which one answered.

The recording is the point. `coverage: "manual"` is a claim about where a number
came from, and without the chain naming the provider on every series it would be
an assumption -- the same class of mistake as a metric that reports a method it
did not use.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from app.providers.base import PricePoint, PriceSeries
from app.providers.chain import PriceChain
from app.providers.manual import ManualPrices

D = Decimal

class Stub:
    """A `PriceProvider` that answers for the symbols it was told about."""

    def __init__(self, name: str, answers: dict[str, PriceSeries]) -> None:
        self.name = name
        self._answers = answers
        self.full_calls: list[str] = []
        self.since_calls: list[tuple[str, date]] = []

    def full_series(self, symbol: str) -> PriceSeries | None:
        self.full_calls.append(symbol)
        return self._answers.get(symbol)

    def series_since(self, symbol: str, since: date) -> PriceSeries | None:
        self.since_calls.append((symbol, since))
        return self._answers.get(symbol)

def priced(symbol: str, source: str) -> PriceSeries:
    return PriceSeries(
        symbol=symbol,
        currency="EUR",
        source=source,
        points=(PricePoint(date(2025, 1, 6), D("12.50"), D("12.50")),),
    )

def manual_file(tmp_path: Path) -> ManualPrices:
    path = tmp_path / "manual_prices.csv"
    path.write_text(
        "isin,date,close,currency\nUS0000000404,2025-01-06,3.20,USD\n", encoding="utf-8"
    )
    return ManualPrices.load(path)

class TestOrder:
    def test_takes_the_first_provider_that_answers(self, tmp_path: Path) -> None:
        first = Stub("first", {"EXA.AS": priced("EXA.AS", "first")})
        second = Stub("second", {"EXA.AS": priced("EXA.AS", "second")})
        chain = PriceChain([first, second], manual_file(tmp_path))

        found = chain.series("NL0000000001", "EXA.AS", since=None)

        assert found is not None
        assert found.source == "first"
        assert second.full_calls == []

    def test_falls_through_a_provider_that_has_nothing(self, tmp_path: Path) -> None:
        empty = Stub("empty", {})
        second = Stub("second", {"EXA.AS": priced("EXA.AS", "second")})
        chain = PriceChain([empty, second], manual_file(tmp_path))

        found = chain.series("NL0000000001", "EXA.AS", since=None)

        assert found is not None and found.source == "second"

    def test_falls_through_a_provider_that_answers_with_no_points(self, tmp_path: Path) -> None:
        """An empty series is not an answer. Accepting it would leave an
        instrument silently unpriced with no fallback attempted."""
        hollow = Stub(
            "hollow",
            {"EXA.AS": PriceSeries("EXA.AS", "EUR", "hollow", ())},
        )
        second = Stub("second", {"EXA.AS": priced("EXA.AS", "second")})
        chain = PriceChain([hollow, second], manual_file(tmp_path))

        found = chain.series("NL0000000001", "EXA.AS", since=None)

        assert found is not None and found.source == "second"

class TestManual:
    def test_goes_straight_to_manual_when_the_answer_file_said_so(self, tmp_path: Path) -> None:
        """`symbol: manual` in `instrument_symbols.yaml` routes here. No provider
        is called, because the operator has already said none of them can help."""
        provider = Stub("provider", {"EXA.AS": priced("EXA.AS", "provider")})
        chain = PriceChain([provider], manual_file(tmp_path))

        found = chain.series("US0000000404", None, since=None)

        assert found is not None
        assert found.source == "manual"
        assert provider.full_calls == []

    def test_manual_is_the_last_resort_when_no_provider_answers(self, tmp_path: Path) -> None:
        chain = PriceChain([Stub("empty", {})], manual_file(tmp_path))
        found = chain.series("US0000000404", "EXA.AS", since=None)
        assert found is not None and found.source == "manual"

    def test_returns_nothing_when_nobody_can_answer(self, tmp_path: Path) -> None:
        """Not an exception and not an empty series: None is what the caller
        turns into `coverage: "missing"`, and a day it touches values `null`."""
        chain = PriceChain([Stub("empty", {})], manual_file(tmp_path))
        assert chain.series("NL0000000009", "EXA.AS", since=None) is None

class TestIncremental:
    def test_asks_for_the_full_history_when_since_is_none(self, tmp_path: Path) -> None:
        provider = Stub("provider", {"EXA.AS": priced("EXA.AS", "provider")})
        PriceChain([provider], manual_file(tmp_path)).series("NL0000000001", "EXA.AS", since=None)
        assert provider.full_calls == ["EXA.AS"]
        assert provider.since_calls == []

    def test_asks_only_for_what_the_cache_lacks_when_given_a_date(self, tmp_path: Path) -> None:
        """M2-9: `fetch-prices` is incremental. A five-year refetch on every run
        is a request the provider has no reason to keep serving."""
        provider = Stub("provider", {"EXA.AS": priced("EXA.AS", "provider")})
        PriceChain([provider], manual_file(tmp_path)).series(
            "NL0000000001", "EXA.AS", since=date(2025, 6, 1)
        )
        assert provider.since_calls == [("EXA.AS", date(2025, 6, 1))]
        assert provider.full_calls == []

    def test_an_incremental_empty_series_is_the_answer_not_a_fallthrough(
        self, tmp_path: Path
    ) -> None:
        """F2: an empty series on a full fetch means "no series at all" -- the
        manual file's case. On an incremental fetch it means "nothing new since
        `since`", which is a real answer. Falling through to the manual file
        here would upsert its whole history over the cached provider rows,
        rewriting `source` to "manual" and flipping fresh days to `coverage:
        "manual"`.

        The manual file here is built so it WOULD answer for `NL0000000001` if
        consulted -- the assertion that `source` is the provider's, not
        `"manual"`, is only meaningful if the manual file was capable of
        answering and simply was not asked.
        """
        empty = Stub("empty", {"EXA.AS": PriceSeries("EXA.AS", "EUR", "empty", ())})
        manual_path = tmp_path / "manual_prices.csv"
        manual_path.write_text(
            "isin,date,close,currency\nNL0000000001,2025-01-06,12.50,EUR\n",
            encoding="utf-8",
        )
        manual = ManualPrices.load(manual_path)
        chain = PriceChain([empty], manual)

        found = chain.series("NL0000000001", "EXA.AS", since=date(2025, 6, 1))

        assert found is not None
        assert found.source == "empty"
        assert found.points == ()

    def test_an_incremental_fetch_still_falls_through_a_provider_that_knows_nothing(
        self, tmp_path: Path
    ) -> None:
        """A bare `None` from the provider -- it does not recognise the symbol
        at all -- is the one case that still justifies the manual fallback,
        even incrementally."""
        chain = PriceChain([Stub("empty", {})], manual_file(tmp_path))

        found = chain.series("US0000000404", "EXA.AS", since=date(2025, 6, 1))

        assert found is not None and found.source == "manual"

    def test_a_full_fetch_empty_series_still_falls_through_to_manual(
        self, tmp_path: Path
    ) -> None:
        """The `since=None` behaviour from `TestOrder` above, unchanged: this is
        the guard the F2 fix must not touch."""
        hollow = Stub("hollow", {"EXA.AS": PriceSeries("EXA.AS", "EUR", "hollow", ())})
        chain = PriceChain([hollow], manual_file(tmp_path))

        found = chain.series("US0000000404", "EXA.AS", since=None)

        assert found is not None and found.source == "manual"

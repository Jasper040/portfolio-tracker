"""`config/manual_prices.csv` -- the fallback for instruments with no public series.

Some holdings have no series on any free tier: a private company, an exchange
microcap, an instrument quoted on a venue nobody indexes. The parent doc
predicted which ones; the spike found a different set, so the list is a fact
about the data rather than a constant, and it belongs in the quarantine's output.

The loader is strict for the same reason `load_resolutions` is: a hand-edited
file whose typos are interpreted rather than rejected produces an import that
succeeds and is wrong. A price is the one number in this app with no cross-check
at all, so a misplaced decimal point here reaches the chart unchallenged.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.providers.manual import MalformedManualPrices, ManualPrices

D = Decimal

GOOD = """isin,date,close,currency
NL0000000001,2025-01-06,12.50,EUR
NL0000000001,2025-01-07,12.75,EUR
US0000000404,2025-01-06,3.20,USD
"""

def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "manual_prices.csv"
    path.write_text(body, encoding="utf-8")
    return path

class TestLoading:
    def test_a_missing_file_holds_nothing(self, tmp_path: Path) -> None:
        """The normal state of a fresh checkout. Not an error -- the file is
        written by answering the first quarantine."""
        prices = ManualPrices.load(tmp_path / "absent.csv")
        assert prices.isins == frozenset()
        assert prices.series("NL0000000001") is None

    def test_reads_prices_as_exact_decimals(self, tmp_path: Path) -> None:
        prices = ManualPrices.load(write(tmp_path, GOOD))
        found = prices.series("NL0000000001")
        assert found is not None
        assert [p.close_unadjusted for p in found.points] == [D("12.50"), D("12.75")]

    def test_a_hand_typed_price_is_its_own_total_return(self, tmp_path: Path) -> None:
        """There is no dividend adjustment to apply to a number somebody typed,
        so both closes hold the same value. Leaving `close_adjusted` empty would
        make `total_return_series()` silently skip the instrument."""
        found = ManualPrices.load(write(tmp_path, GOOD)).series("US0000000404")
        assert found is not None
        assert found.points[0].close_adjusted == found.points[0].close_unadjusted

    def test_records_manual_as_the_source(self, tmp_path: Path) -> None:
        """This is the whole mechanism behind `coverage: "manual"`."""
        found = ManualPrices.load(write(tmp_path, GOOD)).series("NL0000000001")
        assert found is not None
        assert found.source == "manual"

    def test_carries_the_currency_the_operator_stated(self, tmp_path: Path) -> None:
        found = ManualPrices.load(write(tmp_path, GOOD)).series("US0000000404")
        assert found is not None
        assert found.currency == "USD"

    def test_orders_points_by_date_whatever_order_the_file_is_in(self, tmp_path: Path) -> None:
        prices = ManualPrices.load(
            write(
                tmp_path,
                "isin,date,close,currency\n"
                "NL0000000001,2025-01-07,12.75,EUR\n"
                "NL0000000001,2025-01-06,12.50,EUR\n",
            )
        )
        found = prices.series("NL0000000001")
        assert found is not None
        assert [p.on for p in found.points] == [date(2025, 1, 6), date(2025, 1, 7)]

    def test_lists_which_instruments_it_answers_for(self, tmp_path: Path) -> None:
        prices = ManualPrices.load(write(tmp_path, GOOD))
        assert prices.isins == frozenset({"NL0000000001", "US0000000404"})

class TestRejections:
    @pytest.mark.parametrize(
        ("body", "fragment"),
        [
            ("isin,day,close,currency\nNL0000000001,2025-01-06,12.50,EUR\n", "header"),
            ("isin,date,close,currency\nNL0000000001,06-01-2025,12.50,EUR\n", "date"),
            ("isin,date,close,currency\nNL0000000001,2025-01-06,twelve,EUR\n", "close"),
            ("isin,date,close,currency\nNL0000000001,2025-01-06,12.50,\n", "currency"),
            ("isin,date,close,currency\n,2025-01-06,12.50,EUR\n", "isin"),
        ],
    )
    def test_names_the_file_the_line_and_the_field(
        self, tmp_path: Path, body: str, fragment: str
    ) -> None:
        path = write(tmp_path, body)
        with pytest.raises(MalformedManualPrices) as raised:
            ManualPrices.load(path)
        message = str(raised.value)
        assert str(path) in message
        assert fragment in message

    def test_refuses_two_prices_for_one_instrument_and_day(self, tmp_path: Path) -> None:
        """Two answers to "what was it worth that day" is not a tie-break."""
        body = (
            "isin,date,close,currency\n"
            "NL0000000001,2025-01-06,12.50,EUR\n"
            "NL0000000001,2025-01-06,13.50,EUR\n"
        )
        with pytest.raises(MalformedManualPrices, match="twice"):
            ManualPrices.load(write(tmp_path, body))

    def test_refuses_two_currencies_for_one_instrument(self, tmp_path: Path) -> None:
        body = (
            "isin,date,close,currency\n"
            "NL0000000001,2025-01-06,12.50,EUR\n"
            "NL0000000001,2025-01-07,13.50,USD\n"
        )
        with pytest.raises(MalformedManualPrices, match="currency"):
            ManualPrices.load(write(tmp_path, body))

    def test_refuses_a_negative_price(self, tmp_path: Path) -> None:
        body = "isin,date,close,currency\nNL0000000001,2025-01-06,-12.50,EUR\n"
        with pytest.raises(MalformedManualPrices, match="close"):
            ManualPrices.load(write(tmp_path, body))

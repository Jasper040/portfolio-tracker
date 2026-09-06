"""The operator's half of the quarantine (design doc Sec 6.3).

A refusal is only useful if it says what to do about it. These tests pin the exit
code -- so the import can gate a script -- and pin that the message carries the
exact keys the resolutions file has to be written against, because retyping a key
from memory is how an answer silently fails to apply.
"""

from __future__ import annotations

import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.cli import app
from app.settings import get_settings

GOLDEN = Path(__file__).parents[1] / "golden"

SPLIT_KEY = "NL0000000003:2025-01-17:100.00"
PRODUCT_CHANGE_KEY = "US0000000002:2025-01-18:100.00"

RESOLVE_BOTH = f"""\
resolutions:
  - key: {SPLIT_KEY}
    treatment: corporate_action
    note: 10-for-1 split
  - key: {PRODUCT_CHANGE_KEY}
    treatment: corporate_action
"""

runner = CliRunner()


@pytest.fixture(autouse=True)
def database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'ledger.sqlite'}")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def export(tmp_path: Path) -> Path:
    directory = tmp_path / "degiro-export"
    directory.mkdir()
    shutil.copy(GOLDEN / "degiro_transactions_golden.csv", directory / "Transactions.csv")
    shutil.copy(GOLDEN / "degiro_account_golden.csv", directory / "Account.csv")
    return directory


def _resolutions(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "corporate_actions.yaml"
    path.write_text(body, encoding="utf-8")
    return path


class TestImport:
    def test_refuses_and_exits_non_zero_while_a_candidate_is_open(
        self, export: Path, tmp_path: Path
    ) -> None:
        result = runner.invoke(
            app, ["import", str(export), "--resolutions", str(tmp_path / "absent.yaml")]
        )
        assert result.exit_code == 1

    def test_names_the_exact_keys_to_answer(self, export: Path, tmp_path: Path) -> None:
        """The key is the join between the message and the file the operator edits.
        Printing a summary without it turns a two-minute fix into a guess."""
        result = runner.invoke(
            app, ["import", str(export), "--resolutions", str(tmp_path / "absent.yaml")]
        )
        assert SPLIT_KEY in result.output
        assert PRODUCT_CHANGE_KEY in result.output

    def test_imports_once_every_candidate_is_answered(self, export: Path, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            ["import", str(export), "--resolutions", str(_resolutions(tmp_path, RESOLVE_BOTH))],
        )
        assert result.exit_code == 0
        assert "inserted 32" in result.output

    def test_a_second_import_inserts_nothing(self, export: Path, tmp_path: Path) -> None:
        resolutions = str(_resolutions(tmp_path, RESOLVE_BOTH))
        runner.invoke(app, ["import", str(export), "--resolutions", resolutions])
        again = runner.invoke(app, ["import", str(export), "--resolutions", resolutions])
        assert again.exit_code == 0
        assert "inserted 0" in again.output

    def test_a_missing_account_export_is_named_not_traced(
        self, export: Path, tmp_path: Path
    ) -> None:
        (export / "Account.csv").unlink()
        result = runner.invoke(
            app, ["import", str(export), "--resolutions", str(tmp_path / "absent.yaml")]
        )
        assert result.exit_code == 2
        assert "Account.csv" in result.stderr


class TestReview:
    def test_lists_the_open_candidates(self, export: Path, tmp_path: Path) -> None:
        runner.invoke(app, ["import", str(export), "--resolutions", str(tmp_path / "absent.yaml")])
        result = runner.invoke(app, ["review"])
        assert result.exit_code == 0
        assert SPLIT_KEY in result.output
        assert "SPLIT" in result.output

    def test_says_so_when_nothing_is_waiting(self) -> None:
        result = runner.invoke(app, ["review"])
        assert result.exit_code == 0
        assert "no corporate actions" in result.output.casefold()


class TestFetchPrices:
    """The operator's half of the symbol quarantine.

    The message has to carry the ISIN verbatim and the measured ratios, because
    those two are what the operator answers with and what they answer from. A
    refusal that said only "could not resolve 4 instruments" would be a dead end.
    """

    def _stub(self, monkeypatch: pytest.MonkeyPatch, *, resolves_to: str | None) -> None:
        # `app.cli` binds `build_providers` by name at import time
        # (`from app.ingest.prices import ... build_providers ...`), so it is
        # `app.cli.build_providers` -- not `app.ingest.prices.build_providers`
        # -- that the command actually calls. Patching the source module would
        # leave the CLI's own reference pointing at the real one, and this test
        # would make real network calls to OpenFIGI and Yahoo.
        from collections.abc import Sequence

        import app.cli as cli_module
        from app.ingest.prices import Providers
        from app.providers.base import PricePoint, PriceSeries, SymbolCandidate
        from app.providers.chain import PriceChain
        from app.providers.manual import ManualPrices

        class Resolver:
            name = "stub"

            def candidates(self, isin: str) -> tuple[SymbolCandidate, ...]:
                return self.candidates_for([isin])[isin]

            def candidates_for(
                self, isins: Sequence[str]
            ) -> dict[str, tuple[SymbolCandidate, ...]]:
                return {
                    isin: (
                        SymbolCandidate(
                            symbol="EXA2S.DE",
                            name="Example 2x Short",
                            exchange_code="GY",
                            source="stub",
                        ),
                    )
                    for isin in isins
                }

        class Prices:
            name = "stub"

            def full_series(self, symbol: str) -> PriceSeries | None:
                close = "20.00" if resolves_to == symbol else "6.00"
                return PriceSeries(
                    symbol=symbol,
                    currency="EUR",
                    source="stub",
                    points=tuple(
                        PricePoint(
                            on=date(2025, 1, day),
                            close_unadjusted=Decimal(close),
                            close_adjusted=Decimal(close),
                        )
                        for day in (6, 7, 8)
                    ),
                )

            def series_since(self, symbol: str, since: date) -> PriceSeries | None:
                return self.full_series(symbol)

        class Fx:
            name = "stub-fx"

            def series(self, from_ccy, to_ccy, *, start, end):
                return None

        prices = Prices()
        monkeypatch.setattr(
            cli_module,
            "build_providers",
            lambda settings: Providers(
                resolver=Resolver(),
                prices=prices,
                chain=PriceChain([prices], ManualPrices(_by_isin={})),
                fx=Fx(),
            ),
        )

    def test_refuses_and_exits_non_zero_while_a_symbol_is_open(
        self, export: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner.invoke(
            app, ["import", str(export), "--resolutions", str(_resolutions(tmp_path, RESOLVE_BOTH))]
        )
        self._stub(monkeypatch, resolves_to=None)
        monkeypatch.setenv("INSTRUMENT_SYMBOLS_PATH", str(tmp_path / "absent.yaml"))
        get_settings.cache_clear()

        result = runner.invoke(app, ["fetch-prices"])

        assert result.exit_code == 1
        assert "must be answered" in result.stdout
        # The stub's candidate symbol appearing in the refusal is the proof
        # that the stub was actually consulted -- not merely that the command
        # exited 1, which a stray real network failure could also produce.
        assert "EXA2S.DE" in result.stdout
        assert "executed price / provider close" in result.stdout

    def test_the_refusal_prints_a_paste_ready_answer_block(
        self, export: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner.invoke(
            app, ["import", str(export), "--resolutions", str(_resolutions(tmp_path, RESOLVE_BOTH))]
        )
        self._stub(monkeypatch, resolves_to=None)
        monkeypatch.setenv("INSTRUMENT_SYMBOLS_PATH", str(tmp_path / "absent.yaml"))
        get_settings.cache_clear()

        result = runner.invoke(app, ["fetch-prices"])

        assert "symbols:" in result.stdout
        assert "  - isin: " in result.stdout

    def test_symbols_says_so_when_nothing_is_waiting(self) -> None:
        assert runner.invoke(app, ["symbols"]).stdout.strip() == "no unresolved symbols"

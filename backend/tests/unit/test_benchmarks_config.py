"""The benchmark set is an answer, not a lookup (M3 section 4.3).

The ISIN-shaped-key test is the one that matters. `config/benchmarks.yaml` is
TRACKED, which is only safe because a key cannot identify a holding. That is a
structural claim, so it gets a structural check rather than a comment asking
people to be careful.
"""

from __future__ import annotations

from decimal import Decimal as D
from pathlib import Path

import pytest

from app.ingest.benchmarks import Benchmark, BenchmarkConfigError, load_benchmarks


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "benchmarks.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_loads_a_well_formed_entry(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        world:
          symbol: AAA.XX
          currency: EUR
          name: A broad world proxy
          ter: "0.20"
        """,
    )
    assert load_benchmarks(path) == (
        Benchmark(key="world", symbol="AAA.XX", currency="EUR",
                  name="A broad world proxy", ter=D("0.20")),
    )


def test_a_missing_file_is_an_empty_set_not_an_error(tmp_path: Path) -> None:
    """No benchmarks configured is a legitimate state -- the overlay is absent
    and the chart still draws. An exception here would make `fetch-prices`
    unusable for anyone who has not written the file yet."""
    assert load_benchmarks(tmp_path / "absent.yaml") == ()


def test_an_isin_shaped_key_is_refused(tmp_path: Path) -> None:
    """The whole reason this file is tracked. See the module docstring."""
    path = write(
        tmp_path,
        """
        NL0000000001:
          symbol: AAA.XX
          currency: EUR
          name: Nope
          ter: "0.20"
        """,
    )
    with pytest.raises(BenchmarkConfigError, match="looks like an ISIN"):
        load_benchmarks(path)


def test_an_isin_embedded_in_a_longer_slug_is_refused(tmp_path: Path) -> None:
    """The gap the whole-key check left open. `benchmark-nl0000000001` IS a
    valid slug -- lowercase letters, digits and hyphens -- so `_SLUG` passed it
    and an anchored ISIN match saw only "not an ISIN".

    The backstop did not hold either: `test_no_real_data_committed.py` matched
    ISINs case-sensitively while `_SLUG` forces lowercase, so a lowercase ISIN
    inside a slug cleared the loader AND the scanner. `_check_key` is the
    entire justification for `config/benchmarks.yaml` being the one tracked
    file in `config/`, so it has to refuse the substring, not just the whole
    string.
    """
    path = write(
        tmp_path,
        """
        benchmark-nl0000000001:
          symbol: AAA.XX
          currency: EUR
          name: Nope
          ter: "0.20"
        """,
    )
    with pytest.raises(BenchmarkConfigError, match="looks like an ISIN"):
        load_benchmarks(path)


def test_an_ordinary_hyphenated_slug_is_still_accepted(tmp_path: Path) -> None:
    """The substring check must not swallow the keys people actually write.
    Hyphens break the run of alphanumerics an ISIN needs, which is why the slug
    charset was kept narrow in the first place."""
    path = write(
        tmp_path,
        """
        broad-world-acc-2:
          symbol: AAA.XX
          currency: EUR
          name: A broad world proxy
          ter: "0.20"
        """,
    )
    (bench,) = load_benchmarks(path)
    assert bench.key == "broad-world-acc-2"


def test_a_key_outside_the_slug_alphabet_is_refused(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        Not A Slug:
          symbol: AAA.XX
          currency: EUR
          name: Nope
          ter: "0.20"
        """,
    )
    with pytest.raises(BenchmarkConfigError, match="slug"):
        load_benchmarks(path)


def test_a_missing_field_names_the_field_and_the_key(tmp_path: Path) -> None:
    path = write(tmp_path, "world:\n  symbol: AAA.XX\n")
    with pytest.raises(BenchmarkConfigError, match="world.*currency"):
        load_benchmarks(path)


def test_the_ter_is_a_decimal_never_a_float(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        world:
          symbol: AAA.XX
          currency: EUR
          name: A broad world proxy
          ter: 0.07
        """,
    )
    (bench,) = load_benchmarks(path)
    assert bench.ter == D("0.07")
    assert isinstance(bench.ter, D)


def test_a_malformed_file_degrades_to_no_benchmarks_rather_than_a_traceback(
    tmp_path, caplog
) -> None:
    """`create_app` must not die because the benchmark file has a typo in it.

    The CLI path was fixed in M3's fix wave -- it names the error and exits 2 --
    but the API path still let `BenchmarkConfigError` escape `create_app` as a
    startup traceback, so a mistake in one optional config file took down the
    ledger, the valuation and the positions table with it.

    Refusing to start would be the wrong repair. M3 section 4.4 already settled
    the principle for the fetch path: a wrong benchmark must not cost the
    instrument phase its five-year backfill. The same reasoning applies here, and
    an absent benchmark set is a state the app already supports -- the comparison
    simply does not render.

    Degrading is not swallowing: the warning names the file and the reason, so
    the operator who wonders where their benchmark went has somewhere to look.
    """
    import logging

    from app.main import load_benchmarks_or_warn

    bad = tmp_path / "benchmarks.yaml"
    bad.write_text("world:\n  symbol: AAA.XX\n", encoding="utf-8")  # no currency/name/ter

    with caplog.at_level(logging.WARNING):
        assert load_benchmarks_or_warn(bad) == ()

    assert "benchmarks.yaml" in caplog.text

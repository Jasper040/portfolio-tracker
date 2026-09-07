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

import random

from app.ingest.source_ref import RefInput, assign_source_refs


def _row(order: str, qty: str, price: str, when: str = "2026-01-10T14:20") -> RefInput:
    return RefInput(
        order_ref=order, trade_datetime=when, isin="NL0000000002", quantity=qty, price=price
    )


def test_identical_rows_get_distinct_refs() -> None:
    """Two byte-identical partial fills are two real trades, not one."""
    rows = [_row("C1", "-5", "12.34"), _row("C1", "-5", "12.34")]
    refs = assign_source_refs(rows)
    assert len(set(refs)) == 2


def test_refs_are_stable_across_reimport() -> None:
    rows = [_row("C1", "-5", "12.34"), _row("C1", "-5", "12.34"), _row("A1", "10", "1.00")]
    assert assign_source_refs(rows) == assign_source_refs(list(rows))


def test_refs_are_stable_when_the_export_reorders_rows() -> None:
    """A re-export with rows in a different order must not create new transactions."""
    rows = [
        _row("C1", "-5", "12.34"),
        _row("C1", "-5", "12.34"),
        _row("A1", "10", "1.00"),
        _row("B1", "-3", "30.00"),
    ]
    baseline = set(assign_source_refs(rows))
    for seed in range(10):
        shuffled = list(rows)
        random.Random(seed).shuffle(shuffled)
        assert set(assign_source_refs(shuffled)) == baseline


def test_different_prices_give_different_refs() -> None:
    rows = [_row("C1", "-5", "145.3000"), _row("C1", "-5", "145.3050")]
    assert len(set(assign_source_refs(rows))) == 2


def test_blank_order_ref_still_yields_a_ref() -> None:
    rows = [_row("", "100", "1.00"), _row("", "-10", "10.00")]
    refs = assign_source_refs(rows)
    assert all(refs) and len(set(refs)) == 2

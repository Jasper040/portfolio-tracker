"""`Coverage` is a checked type, and the severity table has to keep up.

M3-5 made `Coverage` a `Literal` precisely so a fifth value could not be
introduced without every consumer being told. `analytics/quotes.py`'s
`_SEVERITY` is the one table where that guarantee could be lost quietly:
`worst_coverage` subscripts it with whatever it is handed, so a member with no
rank raises `KeyError` inside `max()`, at request time, rather than failing a
type check.

The annotation on `_SEVERITY` is the first half of the fix -- inference widens
the key type to `str`, which mypy is then happy with. This file is the second
half, for a `Coverage` widened somewhere the annotation is not re-checked.
"""

from __future__ import annotations

from typing import get_args

from app.analytics.quotes import _SEVERITY, worst_coverage
from app.models.types import Coverage


def test_the_severity_table_answers_for_every_coverage_value() -> None:
    assert set(_SEVERITY) == set(get_args(Coverage))


def test_worst_coverage_can_rank_every_pair_of_values() -> None:
    """The consequence, exercised rather than reasoned about: every value the
    type admits has to survive the call the table exists for."""
    values: tuple[Coverage, ...] = get_args(Coverage)
    for first in values:
        for second in values:
            assert worst_coverage([first, second]) in values

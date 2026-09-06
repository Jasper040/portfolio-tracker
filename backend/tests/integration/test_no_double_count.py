"""No call path may reach both closes. Parent doc Sec 7.5, made checkable.

Total return computed from dividend-adjusted prices PLUS dividend income counts
dividends twice, and the resulting figure is wrong in a direction that flatters
the portfolio -- which is the worst possible direction for a number nobody will
question.

The guarantee is structural rather than conventional: two columns, two modules,
two accessors. This test watches the boundary between them by reading the
codebase's own syntax tree.

**What is exempt, and why.** The storage and fetch layers legitimately handle
both closes: `models/market.py` declares the columns, `providers/` parses them
off a response, `ingest/prices.py` writes them. Handling both once, at the
boundary, is what makes two columns possible at all. The rule Sec 7.5 states is
about the READ side -- the modules that turn a price into a number a reader
sees -- so the assertion is scoped to `analytics/` and `api/`, where it is not
vacuous.

**Why `_imports` records the dotted alias too.** `from app.analytics import
total_return` names a MODULE, not a symbol, and recording only `node.module`
(`app.analytics`, a package directory with no `.py` file of its own) would dead-end
the walk on exactly the import this test exists to catch -- do not "simplify" it
back to `node.module` alone.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).parents[2] / "app"

UNADJUSTED = "close_unadjusted"
ADJUSTED = "close_adjusted"

#: The read side. Everything here turns a price into a figure someone reads.
READ_SIDE = ("analytics", "api")

def _identifiers(path: Path) -> set[str]:
    """Every name and attribute the module actually USES.

    An AST walk rather than a text search on purpose: a docstring explaining the
    rule mentions both column names, and a test that could be broken by writing
    down why it exists is not a test worth having.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.keyword) and node.arg:
            found.add(node.arg)
    return found

def _read_side_modules() -> list[Path]:
    return sorted(
        path
        for package in READ_SIDE
        for path in (APP / package).rglob("*.py")
        if path.name != "__init__.py"
    )

def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            # `from app.analytics import total_return` names a MODULE, not a
            # symbol. Recording only `app.analytics` dead-ends the walk on a
            # package directory with no .py file -- which would let exactly the
            # import this test exists to catch slip through. The dotted form
            # over-approximates (it also records
            # `app.analytics.valuation.value_series` for a symbol import), and
            # that costs nothing: a name that resolves to no file is skipped by
            # the walk.
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found

def _module_path(module: str) -> Path:
    """The file a dotted module name resolves to: a plain module, or a
    package's `__init__.py`. A package that only re-exports through its
    `__init__.py` would otherwise be a second, unwalked way through."""
    base = APP.parent / module.replace(".", "/")
    as_module = base.with_suffix(".py")
    if as_module.exists():
        return as_module
    return base / "__init__.py"

def _reachable_from(start: str) -> set[str]:
    """Every `app.*` module reachable by following imports from `start`."""
    seen: set[str] = set()
    queue = [start]
    while queue:
        module = queue.pop()
        if module in seen or not module.startswith("app."):
            continue
        seen.add(module)
        path = _module_path(module)
        if not path.exists():
            continue
        queue.extend(_imports(path))
    return seen

def test_the_read_side_modules_exist_so_this_is_not_vacuous() -> None:
    """The guard M1 learned to write. A call-path assertion over an empty set of
    modules passes for the wrong reason."""
    modules = _read_side_modules()
    assert modules
    assert any(path.name == "valuation.py" for path in modules)
    assert any(path.name == "total_return.py" for path in modules)

def test_no_read_side_module_names_both_closes() -> None:
    both = [
        path.relative_to(APP).as_posix()
        for path in _read_side_modules()
        if {UNADJUSTED, ADJUSTED} <= _identifiers(path)
    ]
    assert not both, (
        "these modules reach both the adjusted and unadjusted close, which is how "
        "a dividend gets counted twice: " + ", ".join(both)
    )

def test_valuation_reads_only_the_unadjusted_close() -> None:
    names = _identifiers(APP / "analytics" / "valuation.py")
    assert UNADJUSTED in names
    assert ADJUSTED not in names

def test_total_return_reads_only_the_adjusted_close() -> None:
    names = _identifiers(APP / "analytics" / "total_return.py")
    assert ADJUSTED in names
    assert UNADJUSTED not in names

def test_the_valuation_endpoints_cannot_reach_the_total_return_module() -> None:
    """The call-path half. Two modules that each read one column would still
    double-count if one called the other."""
    for endpoint in ("app.api.routes_valuation", "app.api.routes_positions"):
        assert "app.analytics.total_return" not in _reachable_from(endpoint)

def test_the_total_return_module_does_not_reach_valuation() -> None:
    assert "app.analytics.valuation" not in _reachable_from("app.analytics.total_return")

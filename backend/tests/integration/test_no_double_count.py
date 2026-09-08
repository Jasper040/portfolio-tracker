"""No call path may reach both closes. Parent doc Sec 7.5, made checkable.

Total return computed from dividend-adjusted prices PLUS dividend income counts
dividends twice, and the resulting figure is wrong in a direction that flatters
the portfolio -- which is the worst possible direction for a number nobody will
question.

The guarantee is structural rather than conventional: two columns, two modules,
two accessors. This test watches the boundary between them by reading the
codebase's own syntax tree.

**What is exempt, and why -- named, not inferred.** The storage and fetch
layers legitimately handle both closes once, at the boundary, which is what
makes two columns possible at all: `models/market.py` declares them,
`providers/` parses them off a response, `ingest/prices.py` writes them into
the cache. Those three are named in `EXEMPT_FILES`/`EXEMPT_PACKAGES` below,
each with a comment saying why it is safe.

**Why this walks everything and subtracts the exempt set, rather than
whitelisting `analytics/` and `api/`.** A whitelist only guards the packages
someone remembered to list. A new read-side package -- M3's instrument chart
is the obvious candidate -- would be silently unguarded until someone thought
to add it. Walking `app/` and excluding a small, named, existence-checked
exempt set means a new package is covered the moment it exists, with no edit
to this file required.

**Why `_imports` records the dotted alias too.** `from app.analytics import
total_return` names a MODULE, not a symbol, and recording only `node.module`
(`app.analytics`, a package directory with no `.py` file of its own) would dead-end
the walk on exactly the import this test exists to catch -- do not "simplify" it
back to `node.module` alone.

**Why a relative import is resolved, not skipped.** `from .foo import bar`
carries no absolute module name in `node.module` -- just `"foo"` plus a
`level` counting the leading dots -- so treating `node.module` as already
absolute (or skipping it because it looks unresolvable) silently drops the
edge. Resolving it against the walking file's own package before recording it
closes that blind spot; nothing in this codebase currently uses a relative
import, so this is prophylactic rather than a fix for an observed miss, but a
call path this test exists to catch is exactly the kind of thing that would
route through one first.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).parents[2] / "app"

UNADJUSTED = "close_unadjusted"
ADJUSTED = "close_adjusted"

#: Individual files exempt from the read-side rule, each legitimately handling
#: both closes once, at the boundary. Path is relative to `APP`, forward-slashed.
EXEMPT_FILES = (
    # Declares the two columns. A declaration is not a read: nothing here
    # turns a close into a number a person sees.
    "models/market.py",
    # Upserts both columns into the price cache, once, from a fetched or
    # manual series. This IS the boundary the rest of the rule protects.
    "ingest/prices.py",
)

#: Whole packages exempt for the same reason.
EXEMPT_PACKAGES = (
    # Parses both closes off a provider response (or a manual CSV row) before
    # handing a `PriceSeries` to the boundary above. Nothing here computes a
    # return or a valuation from what it parses.
    "providers",
)

#: Endpoint modules exempt from the CALL-PATH rule below, dotted, each with a
#: comment saying why. Same shape and same reasoning as `EXEMPT_FILES`: an
#: exemption that is named and existence-checked is a decision someone made,
#: whereas an endpoint the guard never looked at is an accident nobody sees.
EXEMPT_ROUTES = (
    # The instrument chart is the one screen that shows both lanes at once, and
    # the M3 design says so in as many words -- "the chart needs both price
    # columns at once where every prior reader needed exactly one". It presents
    # them as two fields of one response: a price line off the unadjusted close,
    # a total-return comparison off the adjusted one. Sec 7.5's harm is SUMMING
    # the two into a third figure, and nothing here sums them.
    #
    # Note what this exemption does not cover. `routes_instrument` reaches no
    # cash balance, so there is no dividend income for an adjusted close to be
    # counted twice against. An endpoint reaching `analytics/valuation.py` AND
    # the total-return module would be the real defect, and would not belong on
    # this list -- it would belong in a bug report.
    "app.api.routes_instrument",
)

def _is_exempt(path: Path) -> bool:
    rel = path.relative_to(APP).as_posix()
    if rel in EXEMPT_FILES:
        return True
    return any(rel == package or rel.startswith(f"{package}/") for package in EXEMPT_PACKAGES)

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
    """Every module under `app/`, except the named, existence-checked exempt
    set above. See the module docstring for why this walks everything rather
    than whitelisting `analytics/` and `api/`."""
    return sorted(
        path
        for path in APP.rglob("*.py")
        if path.name != "__init__.py" and not _is_exempt(path)
    )

def _module_name(path: Path) -> str:
    """The dotted module name `path` would be imported as."""
    rel = path.relative_to(APP.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)

def _containing_package(path: Path) -> str:
    """The dotted package a relative import inside `path` resolves against.

    A package's `__init__.py` resolves relative imports against ITSELF; a
    plain module resolves against its parent package -- the same distinction
    Python's own import machinery makes via `__package__`.
    """
    name = _module_name(path)
    if path.name == "__init__.py":
        return name
    parts = name.split(".")
    return ".".join(parts[:-1])

def _resolve_import_from(path: Path, node: ast.ImportFrom) -> str:
    """The dotted module `node` imports from, resolving a `.`-prefixed
    (relative) import against `path`'s own package rather than skipping it."""
    if not node.level:
        return node.module or ""
    parts = _containing_package(path).split(".") if _containing_package(path) else []
    climb = node.level - 1
    if climb:
        parts = parts[:-climb] if climb <= len(parts) else []
    base = ".".join(parts)
    if node.module:
        return f"{base}.{node.module}" if base else node.module
    return base

def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_import_from(path, node)
            if module:
                found.add(module)
                # `from app.analytics import total_return` names a MODULE, not
                # a symbol. Recording only the module dead-ends the walk on a
                # package directory with no .py file -- which would let
                # exactly the import this test exists to catch slip through.
                # The dotted form over-approximates (it also records
                # `app.analytics.valuation.value_series` for a symbol
                # import), and that costs nothing: a name that resolves to no
                # file is skipped by the walk.
                found.update(f"{module}.{alias.name}" for alias in node.names)
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
    assert any(path.name == "prices.py" for path in modules)
    assert any(path.name == "total_return.py" for path in modules)
    assert any(path.name == "instrument_price.py" for path in modules)
    assert any(path.name == "instrument_return.py" for path in modules)

def test_the_exempt_set_names_files_that_actually_exist() -> None:
    """An exemption for a deleted file is a hole nobody would notice: it would
    silently exempt nothing, while everyone reading this test believes it
    exempts something specific. Same for a package that no longer has any
    `.py` files under it."""
    for rel in EXEMPT_FILES:
        path = APP / rel
        assert path.is_file(), f"EXEMPT_FILES names {rel!r}, which does not exist"
    for package in EXEMPT_PACKAGES:
        directory = APP / package
        assert directory.is_dir(), f"EXEMPT_PACKAGES names {package!r}, which is not a directory"
        assert any(directory.rglob("*.py")), f"{package!r} has no modules to exempt"

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

def test_the_price_reader_reads_only_the_unadjusted_close() -> None:
    """Was `test_valuation_reads_only_the_unadjusted_close` until M3 split
    `valuation.py`. Re-pointed rather than deleted, and deliberately so: after
    the split `valuation.py` names neither column, so the old assertion would
    still have passed -- vacuously, having quietly lost the half that checks the
    column is present SOMEWHERE. An assertion that passes for a new reason is
    not the same assertion."""
    names = _identifiers(APP / "analytics" / "prices.py")
    assert UNADJUSTED in names
    assert ADJUSTED not in names


def test_valuation_no_longer_names_a_close_column_directly() -> None:
    """It reads through `prices.py` now. Asserted so a future edit that inlines
    a column read back into `valuation.py` has to argue with a test."""
    names = _identifiers(APP / "analytics" / "valuation.py")
    assert UNADJUSTED not in names
    assert ADJUSTED not in names

def test_the_adjusted_point_mapper_reads_only_the_adjusted_close() -> None:
    """Was `test_total_return_reads_only_the_adjusted_close` until M6a split
    `total_return.py` a second time. Re-pointed for exactly the reason the
    unadjusted one above was: after the split neither reader names a column --
    both go through `adjusted.py` -- so the old assertion would have passed
    while quietly losing the half that checks the column is read SOMEWHERE."""
    names = _identifiers(APP / "analytics" / "adjusted.py")
    assert ADJUSTED in names
    assert UNADJUSTED not in names


def test_neither_adjusted_reader_names_a_close_column_directly() -> None:
    """Both read through `adjusted.py` now. Asserted so a future edit that
    inlines a column read back into either reader has to argue with a test."""
    for module in ("total_return.py", "benchmark_return.py"):
        names = _identifiers(APP / "analytics" / module)
        assert UNADJUSTED not in names, module
        assert ADJUSTED not in names, module

def _route_modules() -> list[str]:
    """Every endpoint module under `api/`, discovered rather than listed.

    Was a hardcoded pair of `routes_valuation` and `routes_positions` until M6a.
    The module half of this file walks `app/` and subtracts a named exempt set
    precisely so a new read-side module is covered the moment it exists, and
    argues for that shape at length in the docstring above -- but the call-path
    half did not follow its own advice, so `routes_instrument` arrived unguarded
    and stayed that way for the whole of M3. Deriving the list is what makes the
    next endpoint's arrival a decision rather than an omission.
    """
    return sorted(_module_name(path) for path in (APP / "api").glob("routes_*.py"))


def test_the_route_modules_are_discovered_so_this_is_not_vacuous() -> None:
    """The guard M1 learned to write, applied to the derived list. A call-path
    assertion over an empty set of endpoints passes for the wrong reason, and a
    glob that silently matches nothing is exactly how it would get there."""
    modules = _route_modules()
    assert modules
    assert "app.api.routes_valuation" in modules
    assert "app.api.routes_positions" in modules


def test_the_exempt_routes_name_endpoints_that_actually_exist() -> None:
    """An exemption for a deleted or renamed route is a hole nobody would
    notice: it exempts nothing, while everyone reading this file believes it
    exempts something specific. Same assertion, same reason, as the one over
    `EXEMPT_FILES`."""
    modules = _route_modules()
    for route in EXEMPT_ROUTES:
        assert route in modules, f"EXEMPT_ROUTES names {route!r}, which does not exist"


def test_no_route_module_can_reach_the_total_return_module() -> None:
    """The call-path half. Two modules that each read one column would still
    double-count if one called the other."""
    offenders = [
        module
        for module in _route_modules()
        if module not in EXEMPT_ROUTES
        and "app.analytics.total_return" in _reachable_from(module)
    ]
    assert not offenders, (
        "these endpoints can reach the adjusted close through the total-return "
        "module: " + ", ".join(offenders)
    )

def test_the_total_return_module_does_not_reach_valuation() -> None:
    assert "app.analytics.valuation" not in _reachable_from("app.analytics.total_return")


def test_the_benchmark_reader_does_not_reach_the_instrument_reader() -> None:
    """A benchmark is safe next to a valuation path; an instrument is not.

    Reaching an INSTRUMENT's adjusted closes from a path that also values that
    instrument at the unadjusted close plus cash is Sec 7.5's double count.
    Reaching a BENCHMARK's is not: a benchmark is never held, pays the owner
    nothing, and appears in no cash balance, so there is nothing to count twice.

    While both readers shared `total_return.py` no endpoint could have the
    second without the first, which is the position M6a's performance endpoint
    would have been in -- needing a benchmark, and reaching an instrument's
    adjusted closes to get one.

    The `is_file` assertion is not decoration. `_reachable_from` skips a module
    that does not exist, so without it this test passes vacuously for as long as
    `benchmark_return.py` is absent -- which is the whole of the time it would
    be reporting a guarantee nobody had built yet.
    """
    assert (APP / "analytics" / "benchmark_return.py").is_file()
    reachable = _reachable_from("app.analytics.benchmark_return")
    assert "app.analytics.total_return" not in reachable


def test_the_instrument_price_module_cannot_reach_the_adjusted_close() -> None:
    """The call-path half for M3. Two modules that each name one column would
    still double-count if one could call the other."""
    reachable = _reachable_from("app.analytics.instrument_price")
    assert "app.analytics.total_return" not in reachable


def test_the_instrument_return_module_cannot_reach_the_unadjusted_close() -> None:
    """The direction that is easy to breach by accident: `instrument_return`
    legitimately imports `quotes` for FX, and if the unadjusted readers had
    stayed in that file this assertion would fail. That is why M3 split
    `quotes.py` from `prices.py` -- see M3 section 5.1."""
    reachable = _reachable_from("app.analytics.instrument_return")
    assert "app.analytics.prices" not in reachable
    assert "app.analytics.valuation" not in reachable


def test_the_instrument_route_reaches_both_modules_but_names_neither_column() -> None:
    """The composition point. It is allowed to reach both -- that is its job --
    precisely because it computes nothing itself."""
    reachable = _reachable_from("app.api.routes_instrument")
    assert "app.analytics.instrument_price" in reachable
    assert "app.analytics.instrument_return" in reachable
    names = _identifiers(APP / "api" / "routes_instrument.py")
    assert UNADJUSTED not in names
    assert ADJUSTED not in names

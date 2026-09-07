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
    assert any(path.name == "total_return.py" for path in modules)

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

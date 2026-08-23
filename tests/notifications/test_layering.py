"""Enforces the layering rule ``tricksy/notifications/__init__.py`` states in its own docstring:
``tricksy.notifications`` may import ``tricksy.storage.accounts`` and ``boto3``, but nothing else
from ``tricksy.storage`` (no ``repository``, ``codec``, ``replay``) and nothing from any game
engine under ``tricksy.games`` - that is what makes "the notifier cannot see a hand" checkable
rather than merely claimed. The forbidden prefix is the whole ``tricksy.games`` package rather
than the one engine in it, so a second engine is covered the day it is added.

Pulled forward from ROADMAP.md 4.7 into 4.4, since it's cheap and protects exactly the modules
(``records.py``, ``pump.py``) that phase adds, rather than leaving the rule unguarded until 4.7.

Static analysis rather than a runtime import sweep, the same reasoning
``tests/cli/test_layering.py`` gives: it walks the source with ``ast`` instead of actually
importing every module, so it can't be fooled by an import hidden inside a function body that
never runs during a normal test session, and it costs nothing to run either way.
"""

from __future__ import annotations

import ast
from pathlib import Path

import tricksy.notifications

_FORBIDDEN_PREFIXES = ("tricksy.games",)
_ALLOWED_STORAGE = "tricksy.storage.accounts"


def _imported_modules(source_path: Path) -> set[str]:
    """Every dotted module name a file might make available, including the
    ``from tricksy.storage import repository`` form - ``node.module`` alone would miss that one,
    since it names only ``tricksy.storage``, not the submodule the import actually pulls in."""
    tree = ast.parse(source_path.read_text(), filename=str(source_path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


def _is_forbidden(module: str) -> bool:
    if module.startswith("tricksy.storage"):
        return module != _ALLOWED_STORAGE and not module.startswith(f"{_ALLOWED_STORAGE}.")
    return any(
        module == prefix or module.startswith(f"{prefix}.") for prefix in _FORBIDDEN_PREFIXES
    )


def test_no_module_under_notifications_imports_engine_or_non_accounts_storage() -> None:
    notifications_root = Path(tricksy.notifications.__file__).parent
    violations: dict[str, set[str]] = {}
    for source_path in sorted(notifications_root.rglob("*.py")):
        forbidden = {m for m in _imported_modules(source_path) if _is_forbidden(m)}
        if forbidden:
            violations[str(source_path.relative_to(notifications_root))] = forbidden

    assert not violations, f"tricksy.notifications modules importing forbidden layers: {violations}"

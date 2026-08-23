"""ROADMAP.md 3.7: enforces the claim ``tricksy/cli/__init__.py``, ``render.py`` and ``api.py``
each already make in their own docstrings - that no module under ``tricksy.cli`` imports any game
engine under ``tricksy.games``, ``tricksy.storage`` or boto3 (DESIGN.md §7, §11).

The forbidden prefix is the whole ``tricksy.games`` package rather than the one engine in it, so a
second engine is covered the day it is added rather than the day someone remembers this file.

Static analysis rather than a runtime import sweep: it walks the source with ``ast`` instead of
actually importing every module, so it can't be fooled by an import hidden inside a function body
that never runs during a normal test session, and it costs nothing to run either way.
"""

from __future__ import annotations

import ast
from pathlib import Path

import tricksy.cli

_FORBIDDEN_PREFIXES = ("tricksy.games", "tricksy.storage", "boto3")


def _imported_modules(source_path: Path) -> set[str]:
    tree = ast.parse(source_path.read_text(), filename=str(source_path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


def _is_forbidden(module: str) -> bool:
    return any(
        module == prefix or module.startswith(f"{prefix}.") for prefix in _FORBIDDEN_PREFIXES
    )


def test_no_module_under_cli_imports_engine_storage_or_boto3() -> None:
    cli_root = Path(tricksy.cli.__file__).parent
    violations: dict[str, set[str]] = {}
    for source_path in sorted(cli_root.rglob("*.py")):
        forbidden = {m for m in _imported_modules(source_path) if _is_forbidden(m)}
        if forbidden:
            violations[str(source_path.relative_to(cli_root))] = forbidden

    assert not violations, f"tricksy.cli modules importing engine/storage/boto3: {violations}"

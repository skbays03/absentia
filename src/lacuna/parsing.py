"""Tree-sitter parsing for Python sources.

Thin wrapper that hides the (occasionally moving) tree-sitter API surface
from the rest of the engine. Parsers are reused; nothing is cached on disk
in the MVP.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

import tree_sitter_python
from tree_sitter import Language, Node, Parser

_PY_LANGUAGE = Language(tree_sitter_python.language())
_parser = Parser(_PY_LANGUAGE)


_NOISE_DIR_NAMES = frozenset({
    ".git", "__pycache__", ".venv", "venv", "env",
    "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "build", "dist", ".tox", ".eggs", "site",
    ".lacuna",
})


def parse_file(path: Path) -> Node | None:
    """Parse a Python file; return the root AST node, or None on read failure."""
    try:
        source = path.read_bytes()
    except OSError:
        return None
    tree = _parser.parse(source)
    return tree.root_node


def find_python_files(root: Path) -> Iterator[Path]:
    """Yield every .py file under root, skipping common noise directories."""
    for path in root.rglob("*.py"):
        if any(part in _NOISE_DIR_NAMES for part in path.parts):
            continue
        yield path

"""Source-file discovery — language-agnostic.

Per-language parsing lives in the per-language Extractor; this module
just walks the filesystem and yields candidate source paths.
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Iterator


_NOISE_DIR_NAMES = frozenset({
    ".git", "__pycache__", ".venv", "venv", "env",
    "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "build", "dist", ".tox", ".eggs", "site",
    ".lacuna",
    # Common compiled/output dirs across languages
    "target",       # Rust / Java
    ".gradle",      # Java
    ".next", ".nuxt", "out",   # JS/TS frameworks
    ".cargo",       # Rust
    "vendor",       # Go (and others by convention)
})


def find_source_files(
    root: Path, extensions: Iterable[str]
) -> Iterator[Path]:
    """Yield every file under ``root`` whose extension is in ``extensions``,
    skipping common noise directories.

    ``extensions`` should include the leading dot, e.g. ``(".py", ".js")``.
    Comparison is case-insensitive.
    """
    wanted = {ext.lower() for ext in extensions}
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        if path.suffix.lower() not in wanted:
            continue
        if any(part in _NOISE_DIR_NAMES for part in path.parts):
            continue
        yield path

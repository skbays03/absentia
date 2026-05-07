"""Source-file discovery — language-agnostic.

Per-language parsing lives in the per-language Extractor; this module
just walks the filesystem and yields candidate source paths.
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path, PurePosixPath
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


def _matches_any_glob(rel: PurePosixPath, patterns: tuple[str, ...]) -> bool:
    """True iff ``rel`` matches any glob in ``patterns``.

    Uses ``PurePath.full_match`` (3.13+) so ``**`` segments work as
    expected: ``**/vendor/**`` matches a vendor directory at any depth.
    Catches both file-shaped patterns (``*.test.py``) and dir-shaped
    ones (``build/**``).
    """
    if not patterns:
        return False
    return any(rel.full_match(p) for p in patterns)


def find_source_files(
    root: Path,
    extensions: Iterable[str],
    excludes: Iterable[str] = (),
) -> Iterator[Path]:
    """Yield every file under ``root`` whose extension is in ``extensions``,
    skipping common noise directories and any path matching ``excludes``.

    ``extensions`` should include the leading dot, e.g. ``(".py", ".js")``.
    Comparison is case-insensitive.

    ``excludes`` is a sequence of POSIX glob patterns (matched against
    the file's path relative to ``root``) that will be filtered out.
    Empty means "no extra excludes beyond the built-in noise list".
    """
    wanted = {ext.lower() for ext in extensions}
    exclude_patterns = tuple(excludes)
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        if path.suffix.lower() not in wanted:
            continue
        if any(part in _NOISE_DIR_NAMES for part in path.parts):
            continue
        if exclude_patterns:
            try:
                rel = PurePosixPath(path.relative_to(root).as_posix())
            except ValueError:
                rel = PurePosixPath(path.as_posix())
            if _matches_any_glob(rel, exclude_patterns):
                continue
        yield path

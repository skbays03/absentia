"""Extractor registry — discovers built-in and plugin extractors.

In-tree extractors live in this package (``python.py``, etc.). Third-party
extractors register via the ``lacuna.extractors`` entry-point group::

    [project.entry-points."lacuna.extractors"]
    clojure = "lacuna_clojure:ClojureExtractor"

Discovery merges both sources with plugins overriding builtins by name,
so a community extractor for an existing language can replace ours.
"""
from __future__ import annotations

from importlib.metadata import entry_points
from typing import Iterable

from .base import Extractor
from .javascript import JavaScriptExtractor
from .python import PythonExtractor
from .swift import SwiftExtractor


_BUILTIN_EXTRACTORS: tuple[type[Extractor], ...] = (
    PythonExtractor,
    JavaScriptExtractor,
    SwiftExtractor,
)


def discover_extractor_classes() -> dict[str, type[Extractor]]:
    """Return ``{language_name: Extractor class}`` for every available
    extractor. Builtins first; plugins override."""
    available: dict[str, type[Extractor]] = {
        cls.language_name: cls for cls in _BUILTIN_EXTRACTORS
    }
    try:
        for ep in entry_points(group="lacuna.extractors"):
            try:
                cls = ep.load()
            except Exception:
                continue  # broken plugin shouldn't break lacuna
            if isinstance(cls, type) and issubclass(cls, Extractor):
                available[cls.language_name] = cls
    except Exception:
        pass  # entry-point machinery unavailable; rely on builtins only
    return available


def discover_extractors(languages: Iterable[str]) -> dict[str, Extractor]:
    """Instantiate the extractors requested in config. Unknown languages
    are silently skipped (caller can detect by comparing keys)."""
    classes = discover_extractor_classes()
    return {
        name: classes[name]()
        for name in languages
        if name in classes
    }


def extension_dispatch(extractors: dict[str, Extractor]) -> dict[str, Extractor]:
    """Build a ``{file_extension: extractor}`` map from active extractors."""
    by_ext: dict[str, Extractor] = {}
    for extractor in extractors.values():
        for ext in extractor.file_extensions:
            by_ext[ext.lower()] = extractor
    return by_ext


__all__ = [
    "Extractor",
    "JavaScriptExtractor",
    "PythonExtractor",
    "SwiftExtractor",
    "discover_extractor_classes",
    "discover_extractors",
    "extension_dispatch",
]

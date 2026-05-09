"""Extractor registry — discovers built-in and plugin extractors.

In-tree extractors live in this package (``python.py``, etc.). Third-party
extractors register via the ``absentia.extractors`` entry-point group::

    [project.entry-points."absentia.extractors"]
    clojure = "absentia_clojure:ClojureExtractor"

Discovery merges both sources with plugins overriding builtins by name,
so a community extractor for an existing language can replace ours.
"""
from __future__ import annotations

from importlib.metadata import entry_points
from typing import Iterable

from .base import Extractor
from .bash import BashExtractor
from .c import CExtractor
from .cpp import CPlusPlusExtractor
from .csharp import CSharpExtractor
from .go import GoExtractor
from .java import JavaExtractor
from .javascript import JavaScriptExtractor
from .kotlin import KotlinExtractor
from .lua import LuaExtractor
from .php import PhpExtractor
from .python import PythonExtractor
from .ruby import RubyExtractor
from .rust import RustExtractor
from .scala import ScalaExtractor
from .swift import SwiftExtractor
from .typescript import TSXExtractor, TypeScriptExtractor


# Cache-invalidation salt. The file-content hash that ``_scan_incremental``
# uses to decide "do I have a fresh extract for this file?" is salted with
# this fingerprint. Bumping it invalidates every cached entry on the next
# scan — every file gets re-extracted, the fresh extracts pick up whatever
# new feature_kinds / entity kinds / extractor-logic-fixes have shipped,
# and the user sees the new behavior without knowing they did anything.
#
# **Bump this whenever extractor *output* changes.** Refactors that don't
# change emitted features don't need a bump. Examples that DO need one:
#   - new feature_kind in any extractor's FeatureSet
#   - new entity kind emitted by any extractor
#   - bug fix in extractor logic that changes the entity / feature shape
#   - a new built-in extractor language
#
# Keep the bump in the same commit as the extractor change, with a one-
# line comment naming the change. The history of bumps is the audit
# trail for "when did each extractor's output change?"
#
# Bump history:
#   v1 — initial cache-key shape (content-only hash, pre-fingerprint)
#   v2 — has_docstring + has_return_type + has_param_types detectors
#        added to PythonExtractor (commits c73c60b + 3f9f11e)
#   v3 — has_post_init detector added to PythonExtractor for the
#        config-validation gap (Item A on the gap-detector roadmap).
#   v4 — module entity + has_all_export detector added to
#        PythonExtractor for the __all__-export gap (Item B).
#   v5 — call_kwargs feature added to function/method emission for
#        the logging / tracing call-marker gap (Item C).
EXTRACTOR_FINGERPRINT = "v5"


_BUILTIN_EXTRACTORS: tuple[type[Extractor], ...] = (
    PythonExtractor,
    JavaScriptExtractor,
    TypeScriptExtractor,
    TSXExtractor,
    RustExtractor,
    GoExtractor,
    JavaExtractor,
    RubyExtractor,
    CSharpExtractor,
    SwiftExtractor,
    CExtractor,
    CPlusPlusExtractor,
    PhpExtractor,
    KotlinExtractor,
    ScalaExtractor,
    LuaExtractor,
    BashExtractor,
)


def discover_extractor_classes() -> dict[str, type[Extractor]]:
    """Return ``{language_name: Extractor class}`` for every available
    extractor. Builtins first; plugins override."""
    available: dict[str, type[Extractor]] = {
        cls.language_name: cls for cls in _BUILTIN_EXTRACTORS
    }
    try:
        for ep in entry_points(group="absentia.extractors"):
            try:
                cls = ep.load()
            except Exception:
                continue  # broken plugin shouldn't break absentia
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
    "EXTRACTOR_FINGERPRINT",
    "BashExtractor",
    "CExtractor",
    "CPlusPlusExtractor",
    "CSharpExtractor",
    "Extractor",
    "GoExtractor",
    "JavaExtractor",
    "JavaScriptExtractor",
    "KotlinExtractor",
    "LuaExtractor",
    "PhpExtractor",
    "PythonExtractor",
    "RubyExtractor",
    "RustExtractor",
    "ScalaExtractor",
    "SwiftExtractor",
    "TSXExtractor",
    "TypeScriptExtractor",
    "discover_extractor_classes",
    "discover_extractors",
    "extension_dispatch",
]

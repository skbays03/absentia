"""The Extractor protocol — one per language.

An Extractor owns the language's parser and the AST → Entity/FeatureSet
translation. Lacuna's engine is otherwise language-agnostic: selectors,
mining, and storage all operate on Entity + FeatureSet without caring
which language produced them.

To add a language, implement this ABC and register it via the
``lacuna.extractors`` entry-point group:

    [project.entry-points."lacuna.extractors"]
    clojure = "lacuna_clojure:ClojureExtractor"
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import ClassVar

from tree_sitter import Node

from ..entities import Entity, FeatureSet


class Extractor(ABC):
    """Abstract base for per-language extractors.

    Subclasses must set the two class variables and implement ``parse``
    and ``extract``. The ABC keeps the contract explicit; lacuna's
    discovery code handles the rest.
    """

    #: Canonical language name used in config and CLI flags ("python", "javascript").
    language_name: ClassVar[str]

    #: File extensions this extractor handles, lowercase, with leading dot.
    #: Example: ``(".py", ".pyw")`` or ``(".ts", ".tsx")``.
    file_extensions: ClassVar[tuple[str, ...]]

    @abstractmethod
    def parse(self, source: bytes) -> Node:
        """Parse source bytes; return the root AST node."""

    @abstractmethod
    def extract(
        self, root: Node, file_path: str
    ) -> Iterable[tuple[Entity, FeatureSet]]:
        """Walk the AST and yield (entity, features) pairs.

        ``file_path`` is the POSIX-style path relative to the corpus root,
        used to build qualified names.
        """

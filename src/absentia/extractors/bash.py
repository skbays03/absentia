"""Bash / Shell extractor.

Functions only — shell has no classes, no annotations, and most
expressions are commands. We extract:

  - ``function_definition`` → kind="function", qualified_name="x.sh::name"
  - ``command`` invocations inside function bodies → calls feature

External commands (``echo``, ``make``, ``rm``) are valid call targets;
that's intentional. Mining "every deploy script calls ``set -e``" is
a real shell-script convention check.
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import ClassVar

import tree_sitter_bash
from tree_sitter import Language, Node, Parser, Query, QueryCursor

from ..entities import Entity, FeatureSet, clean_call_name
from .base import Extractor


_BASH_LANGUAGE = Language(tree_sitter_bash.language())
_CALLS_QUERY = Query(_BASH_LANGUAGE, "(command) @call")


class BashExtractor(Extractor):
    language_name: ClassVar[str] = "bash"
    file_extensions: ClassVar[tuple[str, ...]] = (".sh", ".bash")

    def __init__(self) -> None:
        self._parser = Parser(_BASH_LANGUAGE)

    def parse(self, source: bytes) -> Node:
        return self._parser.parse(source).root_node

    def extract(
        self, root: Node, file_path: str
    ) -> Iterable[tuple[Entity, FeatureSet]]:
        return extract_bash_entities(root, file_path)


def extract_bash_entities(
    root: Node, file_path: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    for child in root.children:
        if child.type == "function_definition":
            yield _emit_function(child, file_path)


def _emit_function(
    fn_node: Node, file_path: str
) -> tuple[Entity, FeatureSet]:
    name = _name_of(fn_node)
    entity = Entity(
        kind="function",
        qualified_name=f"{file_path}::{name}",
        file_path=file_path,
        line=fn_node.start_point[0] + 1,
    )
    features = FeatureSet(by_kind={
        "calls": frozenset(_walk_calls(fn_node)),
    })
    return entity, features


def _name_of(fn_node: Node) -> str:
    """Bash function names are ``word`` children — the keyword form
    (``function deploy()``) and bare form (``deploy()``) both expose
    the name as a word child."""
    for child in fn_node.children:
        if child.type == "word":
            return child.text.decode("utf-8")
    return "<anonymous>"


def _walk_calls(root: Node) -> Iterator[str]:
    """Bash ``command`` nodes have a ``command_name`` child whose first
    ``word`` is the executable being invoked."""
    cursor = QueryCursor(_CALLS_QUERY)
    for _, captures in cursor.matches(root):
        for node in captures.get("call", ()):
            for sub in node.children:
                if sub.type == "command_name":
                    for grand in sub.children:
                        if grand.type == "word":
                            yield clean_call_name(grand.text.decode("utf-8").strip())
                            break
                    break

"""Feature extractors — turn AST nodes into structural facts about entities.

MVP: extracts only top-level Python functions, with a single feature kind
("decorator"). Class methods, nested functions, and other entity kinds are
deferred. Extractor is a stable seam — additional ones plug in here later.
"""
from __future__ import annotations

from typing import Iterator

from tree_sitter import Node

from .entities import Entity, FeatureSet


def extract_python_functions(
    root: Node,
    file_path: str,
) -> Iterator[tuple[Entity, FeatureSet]]:
    """Yield (entity, features) for every top-level function in the file."""
    for child in root.children:
        if child.type == "function_definition":
            yield _emit_function(child, file_path, decorators=())
        elif child.type == "decorated_definition":
            decorators = tuple(_decorators_of(child))
            for grandchild in child.children:
                if grandchild.type == "function_definition":
                    yield _emit_function(grandchild, file_path, decorators)


def _emit_function(
    fn_node: Node,
    file_path: str,
    decorators: tuple[str, ...],
) -> tuple[Entity, FeatureSet]:
    name_node = fn_node.child_by_field_name("name")
    name = name_node.text.decode("utf-8") if name_node else "<anonymous>"
    line = fn_node.start_point[0] + 1
    entity = Entity(
        kind="function",
        qualified_name=f"{file_path}::{name}",
        file_path=file_path,
        line=line,
    )
    features = FeatureSet(by_kind={"decorator": frozenset(decorators)})
    return entity, features


def _decorators_of(decorated_node: Node) -> Iterator[str]:
    """Extract canonical decorator names (e.g. '@app.route') from a
    decorated_definition node, dropping any (args) suffix."""
    for child in decorated_node.children:
        if child.type != "decorator":
            continue
        text = child.text.decode("utf-8").strip()
        # Strip leading @ and any (args) — keep only the dotted name
        bare = text.lstrip("@").split("(")[0].strip()
        if bare:
            yield "@" + bare

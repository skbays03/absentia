"""C extractor.

Top-level functions and structs. C has no classes, no decorators, no
inheritance — the simplest mainstream language for lacuna's purposes.
Only the ``calls`` feature is emitted on functions.
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import ClassVar

import tree_sitter_c
from tree_sitter import Language, Node, Parser

from ..entities import Entity, FeatureSet
from .base import Extractor


_C_LANGUAGE = Language(tree_sitter_c.language())


class CExtractor(Extractor):
    language_name: ClassVar[str] = "c"
    file_extensions: ClassVar[tuple[str, ...]] = (".c", ".h")

    def __init__(self) -> None:
        self._parser = Parser(_C_LANGUAGE)

    def parse(self, source: bytes) -> Node:
        return self._parser.parse(source).root_node

    def extract(
        self, root: Node, file_path: str
    ) -> Iterable[tuple[Entity, FeatureSet]]:
        return extract_c_entities(root, file_path)


def extract_c_entities(
    root: Node, file_path: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    for child in root.children:
        if child.type == "function_definition":
            yield _emit_function(child, file_path)
        elif child.type == "struct_specifier":
            emitted = _emit_struct(child, file_path)
            if emitted is not None:
                yield emitted


def _emit_function(
    fn_node: Node, file_path: str
) -> tuple[Entity, FeatureSet]:
    name = _function_name(fn_node)
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


def _emit_struct(
    struct_node: Node, file_path: str
) -> tuple[Entity, FeatureSet] | None:
    name_node = None
    for child in struct_node.children:
        if child.type == "type_identifier":
            name_node = child
            break
    if name_node is None:
        return None  # anonymous struct
    name = name_node.text.decode("utf-8")
    entity = Entity(
        kind="struct",
        qualified_name=f"{file_path}::{name}",
        file_path=file_path,
        line=struct_node.start_point[0] + 1,
    )
    return entity, FeatureSet(by_kind={})


def _function_name(fn_node: Node) -> str:
    """A C function_definition has a function_declarator whose first
    identifier child is the function name."""
    declarator = fn_node.child_by_field_name("declarator")
    if declarator is None:
        for child in fn_node.children:
            if child.type == "function_declarator":
                declarator = child
                break
    if declarator is None:
        return "<anonymous>"
    # Pointer-returning functions wrap the inner declarator; recurse.
    while declarator.type != "function_declarator":
        for sub in declarator.children:
            if sub.type in ("function_declarator", "pointer_declarator"):
                declarator = sub
                break
        else:
            return "<anonymous>"
    for child in declarator.children:
        if child.type == "identifier":
            return child.text.decode("utf-8")
    return "<anonymous>"


def _walk_calls(node: Node) -> Iterator[str]:
    for child in node.children:
        if child.type == "call_expression":
            target = child.child_by_field_name("function")
            if target is not None:
                yield target.text.decode("utf-8").strip()
        yield from _walk_calls(child)

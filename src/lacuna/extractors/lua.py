"""Lua extractor.

Functions only — Lua has no classes or annotations. Functions can be
named several ways:

  - ``function helper()`` → kind="function", qualified_name="x.lua::helper"
  - ``function M.greet()`` → kind="function", qualified_name="x.lua::M.greet"
  - ``function M:method()`` → kind="method", qualified_name="x.lua::M.method"

The colon form is method-style sugar (passes ``self`` implicitly).
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import ClassVar

import tree_sitter_lua
from tree_sitter import Language, Node, Parser

from ..entities import Entity, FeatureSet, clean_call_name, walk_subtree
from .base import Extractor


_LUA_LANGUAGE = Language(tree_sitter_lua.language())


class LuaExtractor(Extractor):
    language_name: ClassVar[str] = "lua"
    file_extensions: ClassVar[tuple[str, ...]] = (".lua",)

    def __init__(self) -> None:
        self._parser = Parser(_LUA_LANGUAGE)

    def parse(self, source: bytes) -> Node:
        return self._parser.parse(source).root_node

    def extract(
        self, root: Node, file_path: str
    ) -> Iterable[tuple[Entity, FeatureSet]]:
        return extract_lua_entities(root, file_path)


def extract_lua_entities(
    root: Node, file_path: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    for child in root.children:
        if child.type == "function_declaration":
            yield _emit_function(child, file_path)


def _emit_function(
    fn_node: Node, file_path: str
) -> tuple[Entity, FeatureSet]:
    name, is_method = _function_name_and_kind(fn_node)
    kind = "method" if is_method else "function"
    entity = Entity(
        kind=kind,
        qualified_name=f"{file_path}::{name}",
        file_path=file_path,
        line=fn_node.start_point[0] + 1,
    )
    features = FeatureSet(by_kind={
        "calls": frozenset(_walk_calls(fn_node)),
    })
    return entity, features


def _function_name_and_kind(fn_node: Node) -> tuple[str, bool]:
    """Lua's function_declaration has either an ``identifier``,
    ``dot_index_expression`` (M.greet), or ``method_index_expression``
    (M:method) describing the function's name. The colon form means
    method-style.
    """
    for child in fn_node.children:
        if child.type == "identifier":
            return child.text.decode("utf-8"), False
        if child.type == "dot_index_expression":
            return child.text.decode("utf-8"), False
        if child.type == "method_index_expression":
            text = child.text.decode("utf-8")
            # M:method → M.method (qualified by colon owner)
            return text.replace(":", ".", 1), True
    return "<anonymous>", False


def _walk_calls(root: Node) -> Iterator[str]:
    for node in walk_subtree(root):
        if node.type == "function_call":
            for sub in node.children:
                if sub.type in ("identifier", "dot_index_expression",
                                "method_index_expression"):
                    yield clean_call_name(sub.text.decode("utf-8").strip())
                    break

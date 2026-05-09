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
from tree_sitter import Language, Node, Parser, Query, QueryCursor

from ..entities import Entity, FeatureSet, clean_call_name
from .base import Extractor


_LUA_LANGUAGE = Language(tree_sitter_lua.language())
_CALLS_QUERY = Query(_LUA_LANGUAGE, "(function_call) @call")


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
        elif child.type == "assignment_statement":
            # Top-level `M.foo = function() ... end` — common
            # table-of-functions module pattern. The function's
            # name comes from the variable_list (a `dot_index_
            # expression` for table fields, an `identifier` for
            # plain rebindings).
            emitted = _from_assignment(child, file_path)
            if emitted is not None:
                yield emitted
        elif child.type == "variable_declaration":
            # `local bar = function() ... end` — local-bound
            # function expression. Same shape, one level deeper:
            # variable_declaration → assignment_statement → RHS
            # function_definition.
            for inner in child.children:
                if inner.type == "assignment_statement":
                    emitted = _from_assignment(inner, file_path)
                    if emitted is not None:
                        yield emitted
                    break


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


def _from_assignment(
    assign_node: Node, file_path: str,
) -> tuple[Entity, FeatureSet] | None:
    """Emit a function entity for an `assignment_statement` whose
    right-hand side is a `function_definition`. Returns None for any
    other assignment shape (variable rebinds, table literals, etc.)."""
    var_list = None
    expr_list = None
    for child in assign_node.children:
        if child.type == "variable_list":
            var_list = child
        elif child.type == "expression_list":
            expr_list = child
    if var_list is None or expr_list is None:
        return None
    # The expression must be exactly one function_definition for us
    # to treat it as a function-binding assignment. Multi-target
    # assignments (`a, b = 1, 2`) and non-function values get skipped.
    fn_def = None
    for child in expr_list.children:
        if child.type == "function_definition":
            fn_def = child
            break
    if fn_def is None:
        return None
    # The name comes from the first variable in the variable_list:
    # an `identifier` (free function) or a `dot_index_expression`
    # (`M.foo`). Method-index (`M:foo`) doesn't appear here — Lua
    # parses `M:foo = ...` as a syntax error; method-style
    # definitions only appear via `function M:foo()` syntax.
    name = "<anonymous>"
    is_method = False
    for child in var_list.children:
        if child.type == "identifier":
            name = child.text.decode("utf-8")
            break
        if child.type == "dot_index_expression":
            name = child.text.decode("utf-8")
            is_method = True
            break
    kind = "method" if is_method else "function"
    entity = Entity(
        kind=kind,
        qualified_name=f"{file_path}::{name}",
        file_path=file_path,
        line=fn_def.start_point[0] + 1,
    )
    features = FeatureSet(by_kind={
        "calls": frozenset(_walk_calls(fn_def)),
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
    cursor = QueryCursor(_CALLS_QUERY)
    for _, captures in cursor.matches(root):
        for node in captures.get("call", ()):
            for sub in node.children:
                if sub.type in ("identifier", "dot_index_expression",
                                "method_index_expression"):
                    yield clean_call_name(sub.text.decode("utf-8").strip())
                    break

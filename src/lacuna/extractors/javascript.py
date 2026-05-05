"""JavaScript extractor.

Top-level functions (declared, arrow, function expression), classes,
and methods inside classes. Class inheritance via ``extends`` produces
a ``parent_class`` feature.

Feature kinds emitted:
  - functions/methods: ``calls``
  - classes:           ``parent_class``

Decorators are not extracted here. TC39 decorators are stage-3 and
extremely rare in plain JS; the (eventual) TypeScript extractor
handles the common TS decorator pattern.
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import ClassVar

import tree_sitter_javascript
from tree_sitter import Language, Node, Parser

from ..entities import Entity, FeatureSet, clean_call_name, walk_subtree
from .base import Extractor


_JS_LANGUAGE = Language(tree_sitter_javascript.language())


class JavaScriptExtractor(Extractor):
    language_name: ClassVar[str] = "javascript"
    file_extensions: ClassVar[tuple[str, ...]] = (".js", ".jsx", ".mjs", ".cjs")

    def __init__(self) -> None:
        self._parser = Parser(_JS_LANGUAGE)

    def parse(self, source: bytes) -> Node:
        return self._parser.parse(source).root_node

    def extract(
        self, root: Node, file_path: str
    ) -> Iterable[tuple[Entity, FeatureSet]]:
        return extract_javascript_entities(root, file_path)


def extract_javascript_entities(
    root: Node, file_path: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    for child in root.children:
        yield from _process_top_level(child, file_path)


def _process_top_level(
    node: Node, file_path: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    if node.type == "function_declaration":
        yield _emit_function(node, file_path)
    elif node.type == "class_declaration":
        yield from _emit_class(node, file_path)
    elif node.type == "lexical_declaration":
        # const/let/var declarations may bind arrow functions or function
        # expressions; treat those as named functions.
        for declarator in node.children:
            if declarator.type == "variable_declarator":
                emitted = _from_declarator(declarator, file_path)
                if emitted is not None:
                    yield emitted


def _from_declarator(
    declarator: Node, file_path: str
) -> tuple[Entity, FeatureSet] | None:
    name_node = declarator.child_by_field_name("name")
    value_node = declarator.child_by_field_name("value")
    if name_node is None or value_node is None:
        return None
    if value_node.type not in ("arrow_function", "function_expression"):
        return None
    name = name_node.text.decode("utf-8")
    entity = Entity(
        kind="function",
        qualified_name=f"{file_path}::{name}",
        file_path=file_path,
        line=value_node.start_point[0] + 1,
    )
    features = FeatureSet(by_kind={
        "calls": frozenset(_walk_calls(value_node)),
    })
    return entity, features


def _emit_function(
    fn_node: Node, file_path: str
) -> tuple[Entity, FeatureSet]:
    name_node = fn_node.child_by_field_name("name")
    name = name_node.text.decode("utf-8") if name_node else "<anonymous>"
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


def _emit_class(
    class_node: Node, file_path: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    name_node = class_node.child_by_field_name("name")
    name = name_node.text.decode("utf-8") if name_node else "<anonymous>"
    parents = frozenset(_extends_of(class_node))

    yield (
        Entity(
            kind="class",
            qualified_name=f"{file_path}::{name}",
            file_path=file_path,
            line=class_node.start_point[0] + 1,
        ),
        FeatureSet(by_kind={
            "parent_class": parents,
        }),
    )

    body = class_node.child_by_field_name("body")
    if body is None:
        return
    for member in body.children:
        if member.type == "method_definition":
            yield _emit_method(member, file_path, name)


def _emit_method(
    method_node: Node, file_path: str, class_name: str
) -> tuple[Entity, FeatureSet]:
    name_node = method_node.child_by_field_name("name")
    name = name_node.text.decode("utf-8") if name_node else "<anonymous>"
    entity = Entity(
        kind="method",
        qualified_name=f"{file_path}::{class_name}.{name}",
        file_path=file_path,
        line=method_node.start_point[0] + 1,
    )
    features = FeatureSet(by_kind={
        "calls": frozenset(_walk_calls(method_node)),
    })
    return entity, features


# ── Helpers ──────────────────────────────────────────────────────────


def _extends_of(class_node: Node) -> Iterator[str]:
    """Yield the names of classes the given class extends.

    JS allows only one parent class, but it can be a dotted member
    expression like ``foo.Base``. We treat the full text as the name.
    """
    for child in class_node.children:
        if child.type == "class_heritage":
            for sub in child.children:
                if sub.type in ("identifier", "member_expression"):
                    yield sub.text.decode("utf-8").strip()


def _walk_calls(root: Node) -> Iterator[str]:
    for node in walk_subtree(root):
        if node.type == "call_expression":
            target = node.child_by_field_name("function")
            if target is not None:
                yield clean_call_name(target.text.decode("utf-8").strip())

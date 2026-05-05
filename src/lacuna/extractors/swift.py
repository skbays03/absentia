"""Swift extractor.

Top-level functions, classes, structs, protocols, enums, extensions,
and methods inside them.

Swift's tree-sitter grammar uses ``class_declaration`` as a catch-all
for ``class``, ``struct``, ``extension``, and ``enum`` — the first
keyword child distinguishes them. Only ``protocol`` gets its own
``protocol_declaration`` node type. We map those to entity kinds:

  - class      → ``class``
  - struct     → ``struct``
  - extension  → ``extension``
  - enum       → ``enum``
  - protocol   → ``protocol``
  - func       → ``function`` (top-level) or ``method`` (inside a class-like)

Feature kinds emitted:
  - all entities:    ``decorator`` (Swift attributes, e.g. ``@objc``,
                                    ``@MainActor``, ``@available``)
  - functions/methods: ``calls``
  - class-likes:       ``parent_class`` (inheritance + protocol conformance)
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import ClassVar

import tree_sitter_swift
from tree_sitter import Language, Node, Parser

from ..entities import Entity, FeatureSet, clean_call_name, walk_subtree
from .base import Extractor


_SW_LANGUAGE = Language(tree_sitter_swift.language())


# Map the keyword child that appears inside a class_declaration to the
# entity kind we emit. Default if not in this map: "class".
_CLASSLIKE_KEYWORD_TO_KIND = {
    "class":     "class",
    "struct":    "struct",
    "extension": "extension",
    "enum":      "enum",
}


class SwiftExtractor(Extractor):
    language_name: ClassVar[str] = "swift"
    file_extensions: ClassVar[tuple[str, ...]] = (".swift",)

    def __init__(self) -> None:
        self._parser = Parser(_SW_LANGUAGE)

    def parse(self, source: bytes) -> Node:
        return self._parser.parse(source).root_node

    def extract(
        self, root: Node, file_path: str
    ) -> Iterable[tuple[Entity, FeatureSet]]:
        return extract_swift_entities(root, file_path)


def extract_swift_entities(
    root: Node, file_path: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    for child in root.children:
        yield from _process_top_level(child, file_path)


def _process_top_level(
    node: Node, file_path: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    if node.type == "function_declaration":
        yield _emit_function(node, file_path, container_name=None)
    elif node.type == "class_declaration":
        yield from _emit_classlike(node, file_path)
    elif node.type == "protocol_declaration":
        yield from _emit_protocol(node, file_path)


def _emit_function(
    fn_node: Node, file_path: str, container_name: str | None
) -> tuple[Entity, FeatureSet]:
    name = _function_name(fn_node)
    if container_name:
        kind = "method"
        qualified_name = f"{file_path}::{container_name}.{name}"
    else:
        kind = "function"
        qualified_name = f"{file_path}::{name}"

    entity = Entity(
        kind=kind,
        qualified_name=qualified_name,
        file_path=file_path,
        line=fn_node.start_point[0] + 1,
    )
    features = FeatureSet(by_kind={
        "decorator": frozenset(_attributes_of(fn_node)),
        "calls": frozenset(_walk_calls(fn_node)),
    })
    return entity, features


def _emit_classlike(
    class_node: Node, file_path: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    """Emit a class/struct/extension/enum and any methods inside it."""
    keyword = _first_keyword(class_node, _CLASSLIKE_KEYWORD_TO_KIND)
    kind = _CLASSLIKE_KEYWORD_TO_KIND.get(keyword, "class")

    name = _classlike_name(class_node)
    if name is None:
        return

    yield (
        Entity(
            kind=kind,
            qualified_name=f"{file_path}::{name}",
            file_path=file_path,
            line=class_node.start_point[0] + 1,
        ),
        FeatureSet(by_kind={
            "decorator": frozenset(_attributes_of(class_node)),
            "parent_class": frozenset(_inheritance_of(class_node)),
        }),
    )

    body = _find_body(class_node, "class_body")
    if body is None:
        return
    for member in body.children:
        if member.type == "function_declaration":
            yield _emit_function(member, file_path, container_name=name)


def _emit_protocol(
    proto_node: Node, file_path: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    name = _classlike_name(proto_node)
    if name is None:
        return
    yield (
        Entity(
            kind="protocol",
            qualified_name=f"{file_path}::{name}",
            file_path=file_path,
            line=proto_node.start_point[0] + 1,
        ),
        FeatureSet(by_kind={
            "decorator": frozenset(_attributes_of(proto_node)),
            "parent_class": frozenset(_inheritance_of(proto_node)),
        }),
    )
    # Protocol bodies contain ``protocol_function_declaration`` stubs without
    # bodies; not emitting them as method entities (no calls to mine).


# ── Helpers ──────────────────────────────────────────────────────────


def _first_keyword(node: Node, keyword_set: dict) -> str:
    """Return the first child whose type is in ``keyword_set``, or ''."""
    for child in node.children:
        if child.type in keyword_set:
            return child.type
    return ""


def _classlike_name(node: Node) -> str | None:
    """Return the type's name. Most class-likes use ``type_identifier``;
    extensions wrap it in a ``user_type``."""
    for child in node.children:
        if child.type == "type_identifier":
            return child.text.decode("utf-8")
        if child.type == "user_type":
            for sub in child.children:
                if sub.type == "type_identifier":
                    return sub.text.decode("utf-8")
    return None


def _function_name(fn_node: Node) -> str:
    for child in fn_node.children:
        if child.type == "simple_identifier":
            return child.text.decode("utf-8")
    return "<anonymous>"


def _find_body(node: Node, body_type: str) -> Node | None:
    for child in node.children:
        if child.type == body_type:
            return child
    return None


def _inheritance_of(node: Node) -> Iterator[str]:
    """Yield names of supertypes / conformed protocols.

    Each ``inheritance_specifier`` child wraps a ``user_type`` whose
    leaf is a ``type_identifier``.
    """
    for child in node.children:
        if child.type != "inheritance_specifier":
            continue
        for sub in child.children:
            if sub.type == "user_type":
                for grand in sub.children:
                    if grand.type == "type_identifier":
                        yield grand.text.decode("utf-8")


def _attributes_of(node: Node) -> Iterator[str]:
    """Yield Swift attribute names (``@objc``, ``@MainActor``, etc.).

    Drops any ``(args)`` suffix so ``@available(macOS 11, *)`` becomes
    ``@available`` for grouping purposes — same convention as Python's
    decorator extractor.
    """
    for child in node.children:
        if child.type != "modifiers":
            continue
        for mod in child.children:
            if mod.type == "attribute":
                text = mod.text.decode("utf-8").strip()
                bare = text.lstrip("@").split("(")[0].strip()
                if bare:
                    yield "@" + bare


def _walk_calls(root: Node) -> Iterator[str]:
    """Yield the textual names of every call inside a function body.

    Swift's ``call_expression`` has the callee as its first child — either
    a ``simple_identifier`` (``bar``), a ``navigation_expression``
    (``self.update``, ``Logger.shared.log``), or some other expression.
    We take the callee's text as the call name.
    """
    for node in walk_subtree(root):
        if node.type == "call_expression" and node.children:
            callee = node.children[0]
            text = callee.text.decode("utf-8").strip()
            if text:
                yield clean_call_name(text)

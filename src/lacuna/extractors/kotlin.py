"""Kotlin extractor.

Top-level functions, classes (including ``data class``, ``object``,
``interface``), and methods inside their bodies. Kotlin annotations
(``@Composable``, ``@Test``, ``@Deprecated``) become decorator
features. Class delegation specifiers (``: BaseScreen(), Greetable``)
both feed parent_class — uniform "is_a" treatment.

Feature kinds emitted:
  - all definitions:  ``decorator``
  - functions/methods: ``calls``
  - class-likes:       ``parent_class``
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import ClassVar

import tree_sitter_kotlin
from tree_sitter import Language, Node, Parser

from ..entities import Entity, FeatureSet, clean_call_name, walk_subtree
from .base import Extractor


_KOTLIN_LANGUAGE = Language(tree_sitter_kotlin.language())


class KotlinExtractor(Extractor):
    language_name: ClassVar[str] = "kotlin"
    file_extensions: ClassVar[tuple[str, ...]] = (".kt", ".kts")

    def __init__(self) -> None:
        self._parser = Parser(_KOTLIN_LANGUAGE)

    def parse(self, source: bytes) -> Node:
        return self._parser.parse(source).root_node

    def extract(
        self, root: Node, file_path: str
    ) -> Iterable[tuple[Entity, FeatureSet]]:
        return extract_kotlin_entities(root, file_path)


def extract_kotlin_entities(
    root: Node, file_path: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    for child in root.children:
        if child.type == "function_declaration":
            yield _emit_function(child, file_path, container=None)
        elif child.type == "class_declaration":
            yield from _emit_classlike(child, file_path)
        elif child.type == "object_declaration":
            yield from _emit_classlike(child, file_path, default_kind="object")


def _emit_function(
    fn_node: Node, file_path: str, container: str | None,
) -> tuple[Entity, FeatureSet]:
    name = _name_of(fn_node)
    if container:
        kind = "method"
        qualified_name = f"{file_path}::{container}.{name}"
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
        "decorator": _annotations_of(fn_node),
        "calls": frozenset(_walk_calls(fn_node)),
    })
    return entity, features


def _emit_classlike(
    node: Node, file_path: str, default_kind: str = "class",
) -> Iterator[tuple[Entity, FeatureSet]]:
    name = _name_of(node)
    if name is None:
        return
    kind = _classlike_kind(node, default_kind)

    yield (
        Entity(
            kind=kind,
            qualified_name=f"{file_path}::{name}",
            file_path=file_path,
            line=node.start_point[0] + 1,
        ),
        FeatureSet(by_kind={
            "decorator": _annotations_of(node),
            "parent_class": frozenset(_delegation_targets(node)),
        }),
    )

    body = None
    for child in node.children:
        if child.type == "class_body":
            body = child
            break
    if body is None:
        return
    for member in body.children:
        if member.type == "function_declaration":
            yield _emit_function(member, file_path, container=name)


# ── Helpers ──────────────────────────────────────────────────────────


def _name_of(node: Node) -> str | None:
    """Class/method/function names are ``identifier`` children."""
    for child in node.children:
        if child.type == "identifier":
            return child.text.decode("utf-8")
    return None


def _classlike_kind(node: Node, default: str) -> str:
    """Distinguish ``class``, ``data class``, ``interface``, ``object``."""
    is_data = False
    is_interface = False
    is_object = False
    for child in node.children:
        if child.type == "modifiers":
            for mod in child.children:
                if mod.type == "class_modifier":
                    text = mod.text.decode("utf-8").strip()
                    if text == "data":
                        is_data = True
        elif child.type == "interface":
            is_interface = True
        elif child.type == "object":
            is_object = True
    if is_interface:
        return "interface"
    if is_object:
        return "object"
    if is_data:
        return "data_class"
    return default


def _annotations_of(node: Node) -> frozenset[str]:
    """Walk modifiers > annotation > user_type to collect ``@Annotation``
    names, dropping any ``(args)`` suffix."""
    out: list[str] = []
    for child in node.children:
        if child.type != "modifiers":
            continue
        for mod in child.children:
            if mod.type != "annotation":
                continue
            for sub in mod.children:
                if sub.type == "user_type":
                    text = sub.text.decode("utf-8").strip().split("(")[0]
                    if text:
                        out.append("@" + text)
                    break
                if sub.type == "constructor_invocation":
                    for grand in sub.children:
                        if grand.type == "user_type":
                            text = grand.text.decode("utf-8").strip()
                            if text:
                                out.append("@" + text)
                            break
                    break
    return frozenset(out)


def _delegation_targets(class_node: Node) -> Iterator[str]:
    """``delegation_specifiers > delegation_specifier`` either holds a
    plain ``user_type`` (interface implementation) or a
    ``constructor_invocation`` (calling a base-class constructor)."""
    for child in class_node.children:
        if child.type != "delegation_specifiers":
            continue
        for spec in child.children:
            if spec.type != "delegation_specifier":
                continue
            for sub in spec.children:
                if sub.type == "user_type":
                    yield sub.text.decode("utf-8").strip()
                elif sub.type == "constructor_invocation":
                    for grand in sub.children:
                        if grand.type == "user_type":
                            yield grand.text.decode("utf-8").strip()
                            break


def _walk_calls(root: Node) -> Iterator[str]:
    for node in walk_subtree(root):
        if node.type == "call_expression" and node.children:
            callee = node.children[0]
            text = callee.text.decode("utf-8").strip()
            if text:
                yield clean_call_name(text)

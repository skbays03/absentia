"""PHP extractor.

Top-level functions, classes, interfaces, traits, and methods inside
their bodies. PHP 8 attributes (``#[Route('/api')]``, ``#[Inject]``)
become decorator features. Class ``extends`` and ``implements`` both
feed parent_class — same uniform treatment as TS / Java / C#.

Feature kinds emitted:
  - all definitions: ``decorator``
  - methods/functions: ``calls``
  - classes/interfaces/traits: ``parent_class``
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import ClassVar

import tree_sitter_php
from tree_sitter import Language, Node, Parser

from ..entities import Entity, FeatureSet, clean_call_name
from .base import Extractor


_PHP_LANGUAGE = Language(tree_sitter_php.language_php())


class PhpExtractor(Extractor):
    language_name: ClassVar[str] = "php"
    file_extensions: ClassVar[tuple[str, ...]] = (".php",)

    def __init__(self) -> None:
        self._parser = Parser(_PHP_LANGUAGE)

    def parse(self, source: bytes) -> Node:
        return self._parser.parse(source).root_node

    def extract(
        self, root: Node, file_path: str
    ) -> Iterable[tuple[Entity, FeatureSet]]:
        return extract_php_entities(root, file_path)


def extract_php_entities(
    root: Node, file_path: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    for child in root.children:
        if child.type == "function_definition":
            yield _emit_function(child, file_path, container=None)
        elif child.type == "class_declaration":
            yield from _emit_classlike(child, file_path, "class")
        elif child.type == "interface_declaration":
            yield from _emit_classlike(child, file_path, "interface")
        elif child.type == "trait_declaration":
            yield from _emit_classlike(child, file_path, "trait")


def _emit_function(
    fn_node: Node, file_path: str, container: str | None
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
        "decorator": _attributes_of(fn_node),
        "calls": frozenset(_walk_calls(fn_node)),
    })
    return entity, features


def _emit_classlike(
    node: Node, file_path: str, kind: str,
) -> Iterator[tuple[Entity, FeatureSet]]:
    name = _name_of(node)
    if name is None:
        return
    parents: list[str] = []
    for child in node.children:
        if child.type == "base_clause":
            for sub in child.children:
                if sub.type == "name":
                    parents.append(sub.text.decode("utf-8"))
        elif child.type == "class_interface_clause":
            for sub in child.children:
                if sub.type == "name":
                    parents.append(sub.text.decode("utf-8"))

    yield (
        Entity(
            kind=kind,
            qualified_name=f"{file_path}::{name}",
            file_path=file_path,
            line=node.start_point[0] + 1,
        ),
        FeatureSet(by_kind={
            "decorator": _attributes_of(node),
            "parent_class": frozenset(parents),
        }),
    )

    body = None
    for child in node.children:
        if child.type == "declaration_list":
            body = child
            break
    if body is None:
        return
    for member in body.children:
        if member.type == "method_declaration":
            yield _emit_method(member, file_path, name)


def _emit_method(
    method_node: Node, file_path: str, container_name: str,
) -> tuple[Entity, FeatureSet]:
    name = _name_of(method_node)
    if name is None:
        name = "<anonymous>"
    entity = Entity(
        kind="method",
        qualified_name=f"{file_path}::{container_name}.{name}",
        file_path=file_path,
        line=method_node.start_point[0] + 1,
    )
    features = FeatureSet(by_kind={
        "decorator": _attributes_of(method_node),
        "calls": frozenset(_walk_calls(method_node)),
    })
    return entity, features


# ── Helpers ──────────────────────────────────────────────────────────


def _name_of(node: Node) -> str | None:
    """PHP class/method/function nodes use a ``name`` child for the name."""
    for child in node.children:
        if child.type == "name":
            return child.text.decode("utf-8")
    return None


def _attributes_of(node: Node) -> frozenset[str]:
    """Walk attribute_list > attribute_group > attribute and pull names.
    Args inside the attribute (e.g. ``#[Route('/x')]``) are dropped."""
    out: list[str] = []
    for child in node.children:
        if child.type != "attribute_list":
            continue
        for grp in child.children:
            if grp.type != "attribute_group":
                continue
            for attr in grp.children:
                if attr.type != "attribute":
                    continue
                for sub in attr.children:
                    if sub.type == "name":
                        out.append("#[" + sub.text.decode("utf-8") + "]")
                        break
    return frozenset(out)


def _walk_calls(node: Node) -> Iterator[str]:
    """PHP calls come in several shapes:

    - ``function_call_expression``  : ``foo()``
    - ``member_call_expression``    : ``$obj->method()``  → ``method``
    - ``scoped_call_expression``    : ``Bar::baz()`` → ``Bar.baz``
    - ``object_creation_expression``: ``new Logger()`` → ``new Logger``
    """
    for child in node.children:
        if child.type == "function_call_expression":
            for sub in child.children:
                if sub.type in ("name", "qualified_name"):
                    yield clean_call_name(sub.text.decode("utf-8").strip())
                    break
        elif child.type == "member_call_expression":
            for sub in child.children:
                if sub.type == "name":
                    yield sub.text.decode("utf-8").strip()
                    break
        elif child.type == "scoped_call_expression":
            obj_part = ""
            method_part = ""
            for sub in child.children:
                if sub.type in ("name", "relative_scope"):
                    if obj_part:
                        method_part = sub.text.decode("utf-8").strip()
                        break
                    obj_part = sub.text.decode("utf-8").strip()
            if obj_part and method_part:
                yield clean_call_name(f"{obj_part}.{method_part}")
        elif child.type == "object_creation_expression":
            for sub in child.children:
                if sub.type in ("name", "qualified_name"):
                    yield "new " + sub.text.decode("utf-8").strip()
                    break
        yield from _walk_calls(child)

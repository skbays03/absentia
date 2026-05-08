"""Java extractor.

Top-level classes, interfaces, enums, and methods inside their bodies.
``extends`` and ``implements`` both feed ``parent_class`` (one
"is_a"-style relationship regardless of inheritance vs. interface
conformance).

Java annotations (``@Override``, ``@Deprecated``, ``@Test``,
``@RequestMapping``, etc.) become ``decorator`` features. Both
``marker_annotation`` (``@Foo``) and ``annotation`` (``@Foo(args)``)
are recognized; arg lists are dropped for grouping purposes — same
convention as Python's decorators.

Feature kinds emitted:
  - all definitions: ``decorator``
  - methods:         ``calls``
  - classes/interfaces/enums: ``parent_class``
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import ClassVar

import tree_sitter_java
from tree_sitter import Language, Node, Parser, Query, QueryCursor

from ..entities import Entity, FeatureSet, clean_call_name
from .base import Extractor


_JAVA_LANGUAGE = Language(tree_sitter_java.language())
_CALLS_QUERY = Query(_JAVA_LANGUAGE, """
[
  (method_invocation)
  (object_creation_expression)
] @call
""")


class JavaExtractor(Extractor):
    language_name: ClassVar[str] = "java"
    file_extensions: ClassVar[tuple[str, ...]] = (".java",)

    def __init__(self) -> None:
        self._parser = Parser(_JAVA_LANGUAGE)

    def parse(self, source: bytes) -> Node:
        return self._parser.parse(source).root_node

    def extract(
        self, root: Node, file_path: str
    ) -> Iterable[tuple[Entity, FeatureSet]]:
        return extract_java_entities(root, file_path)


def extract_java_entities(
    root: Node, file_path: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    for child in root.children:
        if child.type == "class_declaration":
            yield from _emit_class(child, file_path)
        elif child.type == "interface_declaration":
            yield from _emit_interface(child, file_path)
        elif child.type == "enum_declaration":
            yield from _emit_enum(child, file_path)


def _emit_class(
    class_node: Node, file_path: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    name = _name_of(class_node)
    if name is None:
        return
    yield (
        Entity(
            kind="class",
            qualified_name=f"{file_path}::{name}",
            file_path=file_path,
            line=class_node.start_point[0] + 1,
        ),
        FeatureSet(by_kind={
            "decorator": _annotations_in_modifiers(class_node),
            "parent_class": frozenset(_class_supertypes(class_node)),
        }),
    )

    body = _find_child(class_node, "class_body")
    if body is None:
        return
    yield from _emit_methods_in_body(body, file_path, name)


def _emit_interface(
    iface_node: Node, file_path: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    name = _name_of(iface_node)
    if name is None:
        return
    yield (
        Entity(
            kind="interface",
            qualified_name=f"{file_path}::{name}",
            file_path=file_path,
            line=iface_node.start_point[0] + 1,
        ),
        FeatureSet(by_kind={
            "decorator": _annotations_in_modifiers(iface_node),
            "parent_class": frozenset(_interface_supertypes(iface_node)),
        }),
    )

    body = _find_child(iface_node, "interface_body")
    if body is None:
        return
    yield from _emit_methods_in_body(body, file_path, name)


def _emit_enum(
    enum_node: Node, file_path: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    name = _name_of(enum_node)
    if name is None:
        return
    yield (
        Entity(
            kind="enum",
            qualified_name=f"{file_path}::{name}",
            file_path=file_path,
            line=enum_node.start_point[0] + 1,
        ),
        FeatureSet(by_kind={
            "decorator": _annotations_in_modifiers(enum_node),
            "parent_class": frozenset(_interface_supertypes(enum_node)),
        }),
    )

    body = _find_child(enum_node, "enum_body")
    if body is None:
        return
    # Enum bodies can have a declarations subsection holding methods.
    decls = _find_child(body, "enum_body_declarations")
    target = decls if decls is not None else body
    yield from _emit_methods_in_body(target, file_path, name)


def _emit_methods_in_body(
    body: Node, file_path: str, container_name: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    for child in body.children:
        if child.type == "method_declaration":
            yield _emit_method(child, file_path, container_name)


def _emit_method(
    method_node: Node, file_path: str, container_name: str
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
        "decorator": _annotations_in_modifiers(method_node),
        "calls": frozenset(_walk_calls(method_node)),
    })
    return entity, features


# ── Helpers ──────────────────────────────────────────────────────────


def _name_of(node: Node) -> str | None:
    """Java class/interface/enum/method nodes use a top-level identifier
    for the name."""
    for child in node.children:
        if child.type == "identifier":
            return child.text.decode("utf-8")
    return None


def _find_child(node: Node, child_type: str) -> Node | None:
    for child in node.children:
        if child.type == child_type:
            return child
    return None


def _annotations_in_modifiers(node: Node) -> frozenset[str]:
    """Walk the modifiers child (if any) and collect annotation names."""
    out: list[str] = []
    modifiers = _find_child(node, "modifiers")
    if modifiers is None:
        return frozenset()
    for mod in modifiers.children:
        if mod.type in ("marker_annotation", "annotation"):
            for sub in mod.children:
                if sub.type == "identifier":
                    out.append("@" + sub.text.decode("utf-8"))
                    break
    return frozenset(out)


def _class_supertypes(class_node: Node) -> Iterator[str]:
    """Class extends + implements both feed parent_class."""
    superclass = _find_child(class_node, "superclass")
    if superclass is not None:
        for sub in superclass.children:
            if sub.type == "type_identifier":
                yield sub.text.decode("utf-8")
            elif sub.type == "generic_type":
                for grand in sub.children:
                    if grand.type == "type_identifier":
                        yield grand.text.decode("utf-8")
                        break

    super_interfaces = _find_child(class_node, "super_interfaces")
    if super_interfaces is not None:
        type_list = _find_child(super_interfaces, "type_list")
        if type_list is not None:
            yield from _types_from_list(type_list)


def _interface_supertypes(iface_node: Node) -> Iterator[str]:
    """Interfaces extend other interfaces; enums implement them."""
    extends = _find_child(iface_node, "extends_interfaces")
    if extends is not None:
        type_list = _find_child(extends, "type_list")
        if type_list is not None:
            yield from _types_from_list(type_list)
    super_interfaces = _find_child(iface_node, "super_interfaces")
    if super_interfaces is not None:
        type_list = _find_child(super_interfaces, "type_list")
        if type_list is not None:
            yield from _types_from_list(type_list)


def _types_from_list(type_list: Node) -> Iterator[str]:
    for child in type_list.children:
        if child.type == "type_identifier":
            yield child.text.decode("utf-8")
        elif child.type == "generic_type":
            for sub in child.children:
                if sub.type == "type_identifier":
                    yield sub.text.decode("utf-8")
                    break


def _walk_calls(root: Node) -> Iterator[str]:
    """Java method invocations and constructor calls.

    ``method_invocation`` nodes have a ``name`` field for the method
    name and an optional ``object`` for the receiver. We yield the
    full text (e.g. ``Math.abs`` or ``foo``).

    ``object_creation_expression`` (``new Foo()``) we capture as
    ``new Foo`` so users can find groups of "things that construct
    Logger" or similar.
    """
    cursor = QueryCursor(_CALLS_QUERY)
    nodes: list[Node] = []
    for _, captures in cursor.matches(root):
        nodes.extend(captures.get("call", ()))
    for node in nodes:
        if node.type == "method_invocation":
            name_node = node.child_by_field_name("name")
            obj_node = node.child_by_field_name("object")
            if obj_node is not None and name_node is not None:
                yield clean_call_name(
                    obj_node.text.decode("utf-8").strip()
                    + "."
                    + name_node.text.decode("utf-8").strip()
                )
            elif name_node is not None:
                yield name_node.text.decode("utf-8").strip()
        elif node.type == "object_creation_expression":
            type_node = node.child_by_field_name("type")
            if type_node is not None:
                yield "new " + type_node.text.decode("utf-8").strip()

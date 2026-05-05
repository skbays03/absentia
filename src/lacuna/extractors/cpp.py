"""C++ extractor.

Top-level functions, classes, structs, and methods inside class bodies.
Inheritance via ``: public Base, private Mixin`` produces parent_class
features; visibility modifiers in the base list are dropped.

Templates wrap inner function/class definitions in ``template_declaration``
nodes — we look one level inside.

C++ attributes (``[[deprecated]]``, ``[[nodiscard]]``) are not extracted
in the MVP — they're a different syntactic shape than the dominant
patterns in other languages.

Feature kinds emitted:
  - functions/methods: ``calls``
  - classes/structs:   ``parent_class``
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import ClassVar

import tree_sitter_cpp
from tree_sitter import Language, Node, Parser

from ..entities import Entity, FeatureSet, clean_call_name, walk_subtree
from .base import Extractor


_CPP_LANGUAGE = Language(tree_sitter_cpp.language())


class CPlusPlusExtractor(Extractor):
    language_name: ClassVar[str] = "cpp"
    file_extensions: ClassVar[tuple[str, ...]] = (
        ".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx", ".c++",
    )

    def __init__(self) -> None:
        self._parser = Parser(_CPP_LANGUAGE)

    def parse(self, source: bytes) -> Node:
        return self._parser.parse(source).root_node

    def extract(
        self, root: Node, file_path: str
    ) -> Iterable[tuple[Entity, FeatureSet]]:
        return extract_cpp_entities(root, file_path)


def extract_cpp_entities(
    root: Node, file_path: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    for child in root.children:
        yield from _process(child, file_path)


def _process(
    node: Node, file_path: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    if node.type == "function_definition":
        yield _emit_function(node, file_path, container=None)
    elif node.type in ("class_specifier", "struct_specifier"):
        emitted = list(_emit_class_like(node, file_path))
        for e in emitted:
            yield e
    elif node.type == "template_declaration":
        # Recurse into the templated definition.
        for child in node.children:
            if child.type in ("function_definition", "class_specifier",
                              "struct_specifier"):
                yield from _process(child, file_path)
    elif node.type == "namespace_definition":
        body = node.child_by_field_name("body")
        if body is None:
            for child in node.children:
                if child.type == "declaration_list":
                    body = child
                    break
        if body is not None:
            for child in body.children:
                yield from _process(child, file_path)


def _emit_function(
    fn_node: Node, file_path: str, container: str | None
) -> tuple[Entity, FeatureSet]:
    name = _function_name(fn_node)
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
        "calls": frozenset(_walk_calls(fn_node)),
    })
    return entity, features


def _emit_class_like(
    class_node: Node, file_path: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    name = _class_name(class_node)
    if name is None:
        return  # forward decl or anonymous
    kind = "struct" if class_node.type == "struct_specifier" else "class"
    parents = frozenset(_base_classes(class_node))

    yield (
        Entity(
            kind=kind,
            qualified_name=f"{file_path}::{name}",
            file_path=file_path,
            line=class_node.start_point[0] + 1,
        ),
        FeatureSet(by_kind={
            "parent_class": parents,
        }),
    )

    body = None
    for child in class_node.children:
        if child.type == "field_declaration_list":
            body = child
            break
    if body is None:
        return
    for member in body.children:
        if member.type == "function_definition":
            yield _emit_function(member, file_path, container=name)
        elif member.type == "template_declaration":
            for sub in member.children:
                if sub.type == "function_definition":
                    yield _emit_function(sub, file_path, container=name)


def _function_name(fn_node: Node) -> str:
    declarator = fn_node.child_by_field_name("declarator")
    if declarator is None:
        for child in fn_node.children:
            if child.type == "function_declarator":
                declarator = child
                break
    if declarator is None:
        return "<anonymous>"
    while declarator.type != "function_declarator":
        for sub in declarator.children:
            if sub.type in ("function_declarator", "pointer_declarator",
                            "reference_declarator"):
                declarator = sub
                break
        else:
            return "<anonymous>"
    for child in declarator.children:
        if child.type in ("identifier", "field_identifier",
                          "qualified_identifier", "destructor_name",
                          "operator_name"):
            return child.text.decode("utf-8")
    return "<anonymous>"


def _class_name(class_node: Node) -> str | None:
    for child in class_node.children:
        if child.type == "type_identifier":
            return child.text.decode("utf-8")
    return None


def _base_classes(class_node: Node) -> Iterator[str]:
    """C++ ``base_class_clause``: ``: [virtual] [access] type, ...``"""
    for child in class_node.children:
        if child.type != "base_class_clause":
            continue
        for sub in child.children:
            if sub.type in ("type_identifier", "qualified_identifier"):
                yield sub.text.decode("utf-8").strip()
            elif sub.type == "template_type":
                for grand in sub.children:
                    if grand.type == "type_identifier":
                        yield grand.text.decode("utf-8").strip()
                        break


def _walk_calls(root: Node) -> Iterator[str]:
    for node in walk_subtree(root):
        if node.type == "call_expression":
            target = node.child_by_field_name("function")
            if target is not None:
                yield clean_call_name(target.text.decode("utf-8").strip())

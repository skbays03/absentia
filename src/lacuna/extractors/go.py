"""Go extractor.

Top-level functions, methods (functions with a receiver), structs,
and interfaces.

Go has no decorators / annotations, so the ``decorator`` feature is
not emitted. Inheritance per se doesn't exist — interfaces are
satisfied structurally — so ``parent_class`` is also not emitted in
this MVP. Struct embedding (an idiom that approximates inheritance)
is left for a future iteration.

Feature kinds emitted:
  - functions/methods: ``calls``
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import ClassVar

import tree_sitter_go
from tree_sitter import Language, Node, Parser

from ..entities import Entity, FeatureSet, clean_call_name, walk_subtree
from .base import Extractor


_GO_LANGUAGE = Language(tree_sitter_go.language())


class GoExtractor(Extractor):
    language_name: ClassVar[str] = "go"
    file_extensions: ClassVar[tuple[str, ...]] = (".go",)

    def __init__(self) -> None:
        self._parser = Parser(_GO_LANGUAGE)

    def parse(self, source: bytes) -> Node:
        return self._parser.parse(source).root_node

    def extract(
        self, root: Node, file_path: str
    ) -> Iterable[tuple[Entity, FeatureSet]]:
        return extract_go_entities(root, file_path)


def extract_go_entities(
    root: Node, file_path: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    for child in root.children:
        if child.type == "function_declaration":
            yield _emit_function(child, file_path)
        elif child.type == "method_declaration":
            method = _emit_method(child, file_path)
            if method is not None:
                yield method
        elif child.type == "type_declaration":
            yield from _emit_type_declaration(child, file_path)


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


def _emit_method(
    method_node: Node, file_path: str
) -> tuple[Entity, FeatureSet] | None:
    """Emit a Go method (function with a receiver). Qualified name is
    ``file.go::ReceiverType.method``."""
    receiver_type = _receiver_type_of(method_node)
    if receiver_type is None:
        return None
    name_node = method_node.child_by_field_name("name")
    name = name_node.text.decode("utf-8") if name_node else "<anonymous>"
    entity = Entity(
        kind="method",
        qualified_name=f"{file_path}::{receiver_type}.{name}",
        file_path=file_path,
        line=method_node.start_point[0] + 1,
    )
    features = FeatureSet(by_kind={
        "calls": frozenset(_walk_calls(method_node)),
    })
    return entity, features


def _emit_type_declaration(
    type_decl: Node, file_path: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    """A ``type_declaration`` may contain one or more ``type_spec`` nodes
    (Go allows ``type ( A int; B string )`` group form). Each spec is a
    distinct named type."""
    for child in type_decl.children:
        if child.type == "type_spec":
            emitted = _emit_type_spec(child, file_path)
            if emitted is not None:
                yield emitted


def _emit_type_spec(
    type_spec: Node, file_path: str
) -> tuple[Entity, FeatureSet] | None:
    name_node = type_spec.child_by_field_name("name")
    if name_node is None:
        # Try positional fallback
        for child in type_spec.children:
            if child.type == "type_identifier":
                name_node = child
                break
    if name_node is None:
        return None
    name = name_node.text.decode("utf-8")

    # The "type" field gives us the underlying shape.
    type_node = type_spec.child_by_field_name("type")
    if type_node is None:
        # Positional fallback
        for child in type_spec.children:
            if child.type in ("struct_type", "interface_type"):
                type_node = child
                break

    if type_node is None or type_node.type == "struct_type":
        kind = "struct"
    elif type_node.type == "interface_type":
        kind = "interface"
    else:
        # Type alias / other; skip for MVP
        return None

    entity = Entity(
        kind=kind,
        qualified_name=f"{file_path}::{name}",
        file_path=file_path,
        line=type_spec.start_point[0] + 1,
    )
    features = FeatureSet(by_kind={})
    return entity, features


# ── Helpers ──────────────────────────────────────────────────────────


def _receiver_type_of(method_node: Node) -> str | None:
    """Extract the receiver type from a method_declaration.

    A Go method looks like ``func (p *Person) Greet()``. The receiver is
    the *first* ``parameter_list`` child, containing a single
    ``parameter_declaration`` whose type is either a ``type_identifier``
    or a ``pointer_type`` wrapping one.
    """
    for child in method_node.children:
        if child.type != "parameter_list":
            continue
        for sub in child.children:
            if sub.type != "parameter_declaration":
                continue
            for type_child in sub.children:
                if type_child.type == "type_identifier":
                    return type_child.text.decode("utf-8")
                if type_child.type == "pointer_type":
                    for pt_child in type_child.children:
                        if pt_child.type == "type_identifier":
                            return pt_child.text.decode("utf-8")
            # First parameter_declaration is the receiver; only inspect it
            return None
        return None
    return None


def _walk_calls(root: Node) -> Iterator[str]:
    """Go's call_expression's first child (the function field) is the
    callee — either an identifier (``helper``), a selector_expression
    (``fmt.Sprintf``, ``p.Name``), or some other expression."""
    for node in walk_subtree(root):
        if node.type == "call_expression":
            target = node.child_by_field_name("function")
            if target is not None:
                yield clean_call_name(target.text.decode("utf-8").strip())

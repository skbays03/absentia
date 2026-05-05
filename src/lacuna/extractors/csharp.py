"""C# extractor.

Top-level classes, interfaces, structs, records, enums, and the
methods inside their bodies. C# attributes (``[Obsolete]``,
``[Serializable]``, ``[Test]``, ``[ApiController]``) feed the
``decorator`` feature; arg lists are dropped for grouping.

C#'s ``base_list`` mixes class inheritance and interface conformance
syntactically — there's no distinction at the AST level. We add all
of them to ``parent_class``, mirroring TS / Java's uniform "is_a"
treatment.

Both block-scoped namespaces (``namespace N { ... }``) and
file-scoped namespaces (``namespace N;`` at the top of a file) are
supported; types nested inside block-scoped namespaces are recursed
into.

Feature kinds emitted:
  - all definitions:         ``decorator``
  - methods:                 ``calls``
  - classes/structs/records/interfaces/enums: ``parent_class``
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import ClassVar

import tree_sitter_c_sharp
from tree_sitter import Language, Node, Parser

from ..entities import Entity, FeatureSet
from .base import Extractor


_CS_LANGUAGE = Language(tree_sitter_c_sharp.language())


# Map declaration node type → entity kind we emit.
_TYPE_DECL_KINDS = {
    "class_declaration":     "class",
    "interface_declaration": "interface",
    "struct_declaration":    "struct",
    "record_declaration":    "record",
    "enum_declaration":      "enum",
}


class CSharpExtractor(Extractor):
    language_name: ClassVar[str] = "csharp"
    file_extensions: ClassVar[tuple[str, ...]] = (".cs",)

    def __init__(self) -> None:
        self._parser = Parser(_CS_LANGUAGE)

    def parse(self, source: bytes) -> Node:
        return self._parser.parse(source).root_node

    def extract(
        self, root: Node, file_path: str
    ) -> Iterable[tuple[Entity, FeatureSet]]:
        return extract_csharp_entities(root, file_path)


def extract_csharp_entities(
    root: Node, file_path: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    yield from _walk_top_level(root.children, file_path)


def _walk_top_level(
    children: Iterable[Node], file_path: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    for node in children:
        if node.type == "namespace_declaration":
            # Block-scoped namespace; recurse into its declaration_list.
            body = _find_child(node, "declaration_list")
            if body is not None:
                yield from _walk_top_level(body.children, file_path)
        elif node.type == "file_scoped_namespace_declaration":
            # No body to recurse — sibling types follow at the file root,
            # which the outer walk already handles.
            continue
        elif node.type in _TYPE_DECL_KINDS:
            yield from _emit_type(node, file_path)


def _emit_type(
    type_node: Node, file_path: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    name = _name_of(type_node)
    if name is None:
        return
    kind = _TYPE_DECL_KINDS[type_node.type]

    yield (
        Entity(
            kind=kind,
            qualified_name=f"{file_path}::{name}",
            file_path=file_path,
            line=type_node.start_point[0] + 1,
        ),
        FeatureSet(by_kind={
            "decorator": _attributes_of(type_node),
            "parent_class": frozenset(_base_types(type_node)),
        }),
    )

    body = _find_child(type_node, "declaration_list")
    if body is None:
        return
    for member in body.children:
        if member.type == "method_declaration":
            yield _emit_method(member, file_path, name)


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
        "decorator": _attributes_of(method_node),
        "calls": frozenset(_walk_calls(method_node)),
    })
    return entity, features


# ── Helpers ──────────────────────────────────────────────────────────


def _name_of(node: Node) -> str | None:
    """Class/method names are ``identifier`` children — first one we see."""
    for child in node.children:
        if child.type == "identifier":
            return child.text.decode("utf-8")
    return None


def _find_child(node: Node, child_type: str) -> Node | None:
    for child in node.children:
        if child.type == child_type:
            return child
    return None


def _attributes_of(node: Node) -> frozenset[str]:
    """Walk every ``attribute_list`` child and pull names from the
    contained ``attribute`` nodes."""
    out: list[str] = []
    for child in node.children:
        if child.type != "attribute_list":
            continue
        for sub in child.children:
            if sub.type != "attribute":
                continue
            for grand in sub.children:
                if grand.type == "identifier":
                    out.append("[" + grand.text.decode("utf-8") + "]")
                    break
                if grand.type == "qualified_name":
                    out.append("[" + grand.text.decode("utf-8") + "]")
                    break
    return frozenset(out)


def _base_types(node: Node) -> Iterator[str]:
    """C# ``base_list`` mixes class and interface bases. We surface them
    all into parent_class — same treatment as TS / Java."""
    base_list = _find_child(node, "base_list")
    if base_list is None:
        return
    for child in base_list.children:
        if child.type in ("identifier", "qualified_name"):
            yield child.text.decode("utf-8").strip()
        elif child.type == "generic_name":
            for sub in child.children:
                if sub.type == "identifier":
                    yield sub.text.decode("utf-8").strip()
                    break


def _walk_calls(node: Node) -> Iterator[str]:
    """C# call expressions are ``invocation_expression`` (the function
    field is the callee — identifier, member_access_expression, etc.)
    and ``object_creation_expression`` for ``new T()`` constructor calls.
    """
    for child in node.children:
        if child.type == "invocation_expression":
            target = child.child_by_field_name("function")
            if target is not None:
                yield target.text.decode("utf-8").strip()
        elif child.type == "object_creation_expression":
            type_node = child.child_by_field_name("type")
            if type_node is not None:
                yield "new " + type_node.text.decode("utf-8").strip()
        yield from _walk_calls(child)

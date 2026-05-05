"""Scala extractor.

Top-level definitions: class, trait, object, case class, def. Class /
trait / object bodies emit methods (def) inside them. Inheritance
via ``extends Foo with Bar with Baz`` produces parent_class features.

Scala annotations (``@deprecated``, ``@inline``, ``@tailrec``) become
decorator features.

Feature kinds emitted:
  - all definitions: ``decorator``
  - methods/functions: ``calls``
  - classes/traits/objects: ``parent_class``
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import ClassVar

import tree_sitter_scala
from tree_sitter import Language, Node, Parser

from ..entities import Entity, FeatureSet, clean_call_name
from .base import Extractor


_SCALA_LANGUAGE = Language(tree_sitter_scala.language())


_TYPE_DECL_KIND = {
    "class_definition":  "class",
    "trait_definition":  "trait",
    "object_definition": "object",
}


class ScalaExtractor(Extractor):
    language_name: ClassVar[str] = "scala"
    file_extensions: ClassVar[tuple[str, ...]] = (".scala", ".sc")

    def __init__(self) -> None:
        self._parser = Parser(_SCALA_LANGUAGE)

    def parse(self, source: bytes) -> Node:
        return self._parser.parse(source).root_node

    def extract(
        self, root: Node, file_path: str
    ) -> Iterable[tuple[Entity, FeatureSet]]:
        return extract_scala_entities(root, file_path)


def extract_scala_entities(
    root: Node, file_path: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    for child in root.children:
        if child.type == "function_definition":
            yield _emit_function(child, file_path, container=None)
        elif child.type in _TYPE_DECL_KIND:
            yield from _emit_typedef(child, file_path, _TYPE_DECL_KIND[child.type])


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


def _emit_typedef(
    node: Node, file_path: str, kind: str,
) -> Iterator[tuple[Entity, FeatureSet]]:
    name = _name_of(node)
    if name is None:
        return

    yield (
        Entity(
            kind=kind,
            qualified_name=f"{file_path}::{name}",
            file_path=file_path,
            line=node.start_point[0] + 1,
        ),
        FeatureSet(by_kind={
            "decorator": _annotations_of(node),
            "parent_class": frozenset(_extends_clause_targets(node)),
        }),
    )

    body = None
    for child in node.children:
        if child.type == "template_body":
            body = child
            break
    if body is None:
        return
    for member in body.children:
        if member.type in ("function_definition", "function_declaration"):
            yield _emit_function(member, file_path, container=name)


# ── Helpers ──────────────────────────────────────────────────────────


def _name_of(node: Node) -> str | None:
    for child in node.children:
        if child.type == "identifier":
            return child.text.decode("utf-8")
    return None


def _annotations_of(node: Node) -> frozenset[str]:
    """Walk direct ``annotation`` children. Their first ``type_identifier``
    is the annotation's name; ``(args)`` are dropped."""
    out: list[str] = []
    for child in node.children:
        if child.type != "annotation":
            continue
        for sub in child.children:
            if sub.type == "type_identifier":
                out.append("@" + sub.text.decode("utf-8"))
                break
    return frozenset(out)


def _extends_clause_targets(class_node: Node) -> Iterator[str]:
    """``extends_clause`` carries ``extends T1 with T2 with T3`` —
    every type_identifier inside it is a parent_class."""
    for child in class_node.children:
        if child.type != "extends_clause":
            continue
        for sub in child.children:
            if sub.type == "type_identifier":
                yield sub.text.decode("utf-8").strip()
            elif sub.type == "generic_type":
                for grand in sub.children:
                    if grand.type == "type_identifier":
                        yield grand.text.decode("utf-8").strip()
                        break


def _walk_calls(node: Node) -> Iterator[str]:
    for child in node.children:
        if child.type == "call_expression":
            target = child.child_by_field_name("function")
            if target is not None:
                yield clean_call_name(target.text.decode("utf-8").strip())
            elif child.children:
                yield clean_call_name(child.children[0].text.decode("utf-8").strip())
        yield from _walk_calls(child)

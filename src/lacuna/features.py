"""Feature extractors — turn AST nodes into structural facts about entities.

Entity kinds extracted today:

  - ``function`` — top-level functions (with ``decorator`` and ``calls`` features)
  - ``class``    — top-level classes (with ``decorator`` and ``parent_class``)
  - ``method``   — functions inside a class body (with ``decorator`` and ``calls``)

Nested functions, async functions, and classes nested inside functions are
out of scope for the MVP. Extractors are a stable seam — additional ones
plug in here.
"""
from __future__ import annotations

from typing import Iterator

from tree_sitter import Node

from .entities import Entity, FeatureSet


def extract_python_entities(
    root: Node,
    file_path: str,
) -> Iterator[tuple[Entity, FeatureSet]]:
    """Yield (entity, features) for top-level functions, classes, and the
    methods inside those classes."""
    for child in root.children:
        yield from _process_top_level(child, file_path)


def _process_top_level(
    node: Node, file_path: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    if node.type == "function_definition":
        yield _emit_function(node, file_path, decorators=())
    elif node.type == "class_definition":
        yield from _emit_class(node, file_path, decorators=())
    elif node.type == "decorated_definition":
        decorators = tuple(_decorators_of(node))
        for child in node.children:
            if child.type == "function_definition":
                yield _emit_function(child, file_path, decorators)
                return
            if child.type == "class_definition":
                yield from _emit_class(child, file_path, decorators)
                return


def _emit_function(
    fn_node: Node,
    file_path: str,
    decorators: tuple[str, ...],
) -> tuple[Entity, FeatureSet]:
    name = _name_of(fn_node)
    entity = Entity(
        kind="function",
        qualified_name=f"{file_path}::{name}",
        file_path=file_path,
        line=fn_node.start_point[0] + 1,
    )
    features = FeatureSet(by_kind={
        "decorator": frozenset(decorators),
        "calls": frozenset(_walk_calls(fn_node)),
    })
    return entity, features


def _emit_class(
    class_node: Node,
    file_path: str,
    decorators: tuple[str, ...],
) -> Iterator[tuple[Entity, FeatureSet]]:
    name = _name_of(class_node)
    parents = frozenset(_superclasses_of(class_node))

    yield (
        Entity(
            kind="class",
            qualified_name=f"{file_path}::{name}",
            file_path=file_path,
            line=class_node.start_point[0] + 1,
        ),
        FeatureSet(by_kind={
            "decorator": frozenset(decorators),
            "parent_class": parents,
        }),
    )

    body = class_node.child_by_field_name("body")
    if body is None:
        return
    for member in body.children:
        yield from _process_class_member(member, file_path, name)


def _process_class_member(
    node: Node, file_path: str, class_name: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    if node.type == "function_definition":
        yield _emit_method(node, file_path, class_name, decorators=())
    elif node.type == "decorated_definition":
        decorators = tuple(_decorators_of(node))
        for child in node.children:
            if child.type == "function_definition":
                yield _emit_method(child, file_path, class_name, decorators)
                return


def _emit_method(
    fn_node: Node,
    file_path: str,
    class_name: str,
    decorators: tuple[str, ...],
) -> tuple[Entity, FeatureSet]:
    name = _name_of(fn_node)
    entity = Entity(
        kind="method",
        qualified_name=f"{file_path}::{class_name}.{name}",
        file_path=file_path,
        line=fn_node.start_point[0] + 1,
    )
    features = FeatureSet(by_kind={
        "decorator": frozenset(decorators),
        "calls": frozenset(_walk_calls(fn_node)),
    })
    return entity, features


# ── Helpers ──────────────────────────────────────────────────────────


def _name_of(definition_node: Node) -> str:
    name_node = definition_node.child_by_field_name("name")
    return name_node.text.decode("utf-8") if name_node else "<anonymous>"


def _walk_calls(node: Node) -> Iterator[str]:
    for child in node.children:
        if child.type == "call":
            target = child.child_by_field_name("function")
            if target is not None:
                yield target.text.decode("utf-8").strip()
        yield from _walk_calls(child)


def _decorators_of(decorated_node: Node) -> Iterator[str]:
    """Canonical decorator names (e.g. ``@app.route``), with any ``(args)``
    suffix dropped."""
    for child in decorated_node.children:
        if child.type != "decorator":
            continue
        text = child.text.decode("utf-8").strip()
        bare = text.lstrip("@").split("(")[0].strip()
        if bare:
            yield "@" + bare


def _superclasses_of(class_node: Node) -> Iterator[str]:
    """Names of the immediate parent classes. Skips ``metaclass=...`` and
    similar keyword arguments."""
    superclasses_node = class_node.child_by_field_name("superclasses")
    if superclasses_node is None:
        return
    for child in superclasses_node.children:
        if child.type in ("identifier", "attribute"):
            yield child.text.decode("utf-8").strip()

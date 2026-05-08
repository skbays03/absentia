"""Rust extractor.

Top-level functions, structs, enums, traits, and impl blocks. Methods
live inside ``impl_item`` declaration_lists and are qualified by the
type they target.

Trait conformance via ``impl Trait for Type`` produces an entity of
kind ``impl`` with the trait as a ``parent_class``. Intrinsic impls
(``impl Type``) are also emitted but with empty parent_class. Both
make their methods discoverable as kind ``method``.

Rust attributes (``#[derive(...)]``, ``#[cfg(...)]``, ``#[serde(...)]``)
sit as ``attribute_item`` *siblings* preceding the item they apply to.
We accumulate pending attributes during the walk and attach them to
the next named item.

Feature kinds emitted:
  - all definitions: ``decorator`` (Rust attributes)
  - functions/methods: ``calls``
  - traits / impls:    ``parent_class`` (supertraits / impl-of-trait)
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import ClassVar

import tree_sitter_rust
from tree_sitter import Language, Node, Parser, Query, QueryCursor

from ..entities import Entity, FeatureSet, clean_call_name
from .base import Extractor


_RS_LANGUAGE = Language(tree_sitter_rust.language())
# Rust calls come in two shapes: regular function calls and macro
# invocations (println!, vec!, etc. — user-visible call-shaped ops).
_CALLS_QUERY = Query(_RS_LANGUAGE, """
[
  (call_expression)
  (macro_invocation)
] @call
""")


class RustExtractor(Extractor):
    language_name: ClassVar[str] = "rust"
    file_extensions: ClassVar[tuple[str, ...]] = (".rs",)

    def __init__(self) -> None:
        self._parser = Parser(_RS_LANGUAGE)

    def parse(self, source: bytes) -> Node:
        return self._parser.parse(source).root_node

    def extract(
        self, root: Node, file_path: str
    ) -> Iterable[tuple[Entity, FeatureSet]]:
        return extract_rust_entities(root, file_path)


# ── Module-level extraction ──────────────────────────────────────────


def extract_rust_entities(
    root: Node, file_path: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    """Walk top-level siblings; accumulate ``attribute_item`` nodes as
    pending attributes attached to the next named item."""
    yield from _walk_with_attributes(root.children, file_path)


def _walk_with_attributes(
    children: Iterable[Node], file_path: str, container: str | None = None,
) -> Iterator[tuple[Entity, FeatureSet]]:
    pending: list[str] = []
    for node in children:
        if node.type == "attribute_item":
            name = _attribute_name(node)
            if name:
                pending.append(name)
            continue

        if node.type == "function_item":
            yield _emit_function(node, file_path, tuple(pending), container)
            pending = []
        elif node.type == "function_signature_item":
            # Trait method signature, no body — emit as method but with no calls.
            yield _emit_function(node, file_path, tuple(pending), container)
            pending = []
        elif node.type == "struct_item":
            yield _emit_struct(node, file_path, tuple(pending))
            pending = []
        elif node.type == "enum_item":
            yield _emit_enum(node, file_path, tuple(pending))
            pending = []
        elif node.type == "trait_item":
            yield from _emit_trait(node, file_path, tuple(pending))
            pending = []
        elif node.type == "impl_item":
            yield from _emit_impl(node, file_path, tuple(pending))
            pending = []
        elif node.type in ("line_comment", "block_comment", "inner_attribute_item"):
            continue
        else:
            pending = []


def _emit_function(
    fn_node: Node, file_path: str,
    decorators: tuple[str, ...], container: str | None,
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
        "decorator": frozenset(decorators),
        "calls": frozenset(_walk_calls(fn_node)),
    })
    return entity, features


def _emit_struct(
    struct_node: Node, file_path: str, decorators: tuple[str, ...]
) -> tuple[Entity, FeatureSet]:
    name = _name_of(struct_node)
    entity = Entity(
        kind="struct",
        qualified_name=f"{file_path}::{name}",
        file_path=file_path,
        line=struct_node.start_point[0] + 1,
    )
    features = FeatureSet(by_kind={
        "decorator": frozenset(decorators),
        "parent_class": frozenset(),  # Rust structs don't inherit
    })
    return entity, features


def _emit_enum(
    enum_node: Node, file_path: str, decorators: tuple[str, ...]
) -> tuple[Entity, FeatureSet]:
    name = _name_of(enum_node)
    entity = Entity(
        kind="enum",
        qualified_name=f"{file_path}::{name}",
        file_path=file_path,
        line=enum_node.start_point[0] + 1,
    )
    features = FeatureSet(by_kind={
        "decorator": frozenset(decorators),
        "parent_class": frozenset(),
    })
    return entity, features


def _emit_trait(
    trait_node: Node, file_path: str, decorators: tuple[str, ...]
) -> Iterator[tuple[Entity, FeatureSet]]:
    name = _name_of(trait_node)
    supertraits = frozenset(_trait_supertraits(trait_node))

    yield (
        Entity(
            kind="trait",
            qualified_name=f"{file_path}::{name}",
            file_path=file_path,
            line=trait_node.start_point[0] + 1,
        ),
        FeatureSet(by_kind={
            "decorator": frozenset(decorators),
            "parent_class": supertraits,
        }),
    )

    # Trait method signatures — emit as methods (no body, empty calls).
    body = _find_child(trait_node, "declaration_list")
    if body is None:
        return
    yield from _walk_with_attributes(body.children, file_path, container=name)


def _emit_impl(
    impl_node: Node, file_path: str, decorators: tuple[str, ...]
) -> Iterator[tuple[Entity, FeatureSet]]:
    """Handle both ``impl Type`` (intrinsic) and ``impl Trait for Type``.

    Trait-conformance impls produce an entity of kind ``impl`` whose
    ``parent_class`` is the trait. Intrinsic impls produce one with
    empty parent_class. Methods inside both flow through as kind
    ``method`` qualified by the implementing type.
    """
    type_idents = [c for c in impl_node.children if c.type == "type_identifier"]
    has_for = any(c.type == "for" for c in impl_node.children)

    if has_for and len(type_idents) >= 2:
        trait_name = type_idents[0].text.decode("utf-8")
        target = type_idents[1].text.decode("utf-8")
        qualified_name = f"{file_path}::{target} (impl {trait_name})"
        parent_classes = frozenset({trait_name})
    elif type_idents:
        target = type_idents[0].text.decode("utf-8")
        qualified_name = f"{file_path}::{target} (impl)"
        parent_classes = frozenset()
    else:
        return  # no recognizable impl target

    yield (
        Entity(
            kind="impl",
            qualified_name=qualified_name,
            file_path=file_path,
            line=impl_node.start_point[0] + 1,
        ),
        FeatureSet(by_kind={
            "decorator": frozenset(decorators),
            "parent_class": parent_classes,
        }),
    )

    body = _find_child(impl_node, "declaration_list")
    if body is None:
        return
    yield from _walk_with_attributes(body.children, file_path, container=target)


# ── Helpers ──────────────────────────────────────────────────────────


def _name_of(item_node: Node) -> str:
    """Return the item's ``identifier`` or ``type_identifier`` name."""
    for child in item_node.children:
        if child.type in ("identifier", "type_identifier"):
            return child.text.decode("utf-8")
    return "<anonymous>"


def _find_child(node: Node, child_type: str) -> Node | None:
    for child in node.children:
        if child.type == child_type:
            return child
    return None


def _trait_supertraits(trait_node: Node) -> Iterator[str]:
    """Yield names of supertraits in ``trait Foo: Bar + Baz`` declarations."""
    in_bounds = False
    for child in trait_node.children:
        if child.type == ":":
            in_bounds = True
            continue
        if child.type == "where_clause" or child.type == "declaration_list":
            break
        if not in_bounds:
            continue
        if child.type == "type_identifier":
            yield child.text.decode("utf-8")
        elif child.type == "trait_bounds":
            for sub in child.children:
                if sub.type == "type_identifier":
                    yield sub.text.decode("utf-8")


def _attribute_name(attr_item: Node) -> str | None:
    """Canonical attribute name (e.g. ``#[derive]``).

    Drops the args inside any ``token_tree``: ``#[derive(Debug, Clone)]``
    becomes ``#[derive]`` for grouping purposes.
    """
    for child in attr_item.children:
        if child.type == "attribute":
            for sub in child.children:
                if sub.type == "identifier":
                    return f"#[{sub.text.decode('utf-8')}]"
            # Some attributes use scoped_identifier (e.g. ``#[serde::skip]``)
            for sub in child.children:
                if sub.type == "scoped_identifier":
                    return f"#[{sub.text.decode('utf-8')}]"
    return None


def _walk_calls(root: Node) -> Iterator[str]:
    """Yield call names from the function-body subtree.

    Catches both regular ``call_expression`` and ``macro_invocation``
    (e.g. ``println!()``, ``vec![]``) — macros are user-visible
    call-shaped operations and worth tracking in the calls feature.
    The tree-sitter Query handles arbitrary nesting depth — no risk
    of Python recursion-limit issues that plagued the previous
    walk_subtree implementation on the Rust compiler's source.
    """
    cursor = QueryCursor(_CALLS_QUERY)
    nodes: list[Node] = []
    for _, captures in cursor.matches(root):
        nodes.extend(captures.get("call", ()))
    for node in nodes:
        if node.type == "call_expression" and node.children:
            target = node.child_by_field_name("function")
            if target is not None:
                yield clean_call_name(target.text.decode("utf-8").strip())
        elif node.type == "macro_invocation":
            for sub in node.children:
                if sub.type in ("identifier", "scoped_identifier"):
                    yield sub.text.decode("utf-8").strip() + "!"
                    break

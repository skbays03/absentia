"""TypeScript / TSX extractor.

Top-level functions (declared, arrow, function expression), classes,
methods, and interfaces. Class ``extends`` produces a ``parent_class``
feature; ``implements`` clauses also feed into ``parent_class`` (we
treat "is_a" as one concept regardless of inheritance vs. structural
conformance — same approach as Swift's protocol conformance).

Decorators are first-class. They appear as sibling ``decorator`` nodes
preceding the item they decorate, sometimes wrapped inside an
``export_statement`` along with the export keyword. We walk siblings
in order and accumulate pending decorators until the next item.

TSX is exposed as a separate extractor (TSXExtractor) because the
.tsx grammar differs from .ts. Both share extraction logic.
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import ClassVar

import tree_sitter_typescript
from tree_sitter import Language, Node, Parser, Query, QueryCursor

from ..entities import Entity, FeatureSet, clean_call_name
from .base import Extractor


_TS_LANGUAGE = Language(tree_sitter_typescript.language_typescript())
_TSX_LANGUAGE = Language(tree_sitter_typescript.language_tsx())

# Two queries — one per grammar — because tree-sitter's Query is
# bound to a specific Language. TS and TSX are sibling grammars
# (TSX adds JSX nodes); the call-shape node names match, but the
# Language IDs differ, so we compile each query separately and the
# extractor passes the right one through.
_TS_CALLS_QUERY = Query(_TS_LANGUAGE, "(call_expression function: (_) @target)")
_TSX_CALLS_QUERY = Query(_TSX_LANGUAGE, "(call_expression function: (_) @target)")


class TypeScriptExtractor(Extractor):
    language_name: ClassVar[str] = "typescript"
    file_extensions: ClassVar[tuple[str, ...]] = (".ts", ".mts", ".cts")
    _calls_query: ClassVar[Query] = _TS_CALLS_QUERY

    def __init__(self) -> None:
        self._parser = Parser(_TS_LANGUAGE)

    def parse(self, source: bytes) -> Node:
        return self._parser.parse(source).root_node

    def extract(
        self, root: Node, file_path: str
    ) -> Iterable[tuple[Entity, FeatureSet]]:
        return extract_typescript_entities(root, file_path, self._calls_query)


class TSXExtractor(TypeScriptExtractor):
    """TSX uses a different grammar than TS — JSX changes the parser."""
    language_name: ClassVar[str] = "tsx"
    file_extensions: ClassVar[tuple[str, ...]] = (".tsx",)
    _calls_query: ClassVar[Query] = _TSX_CALLS_QUERY

    def __init__(self) -> None:
        self._parser = Parser(_TSX_LANGUAGE)


# ── Module-level extraction ──────────────────────────────────────────


_ITEM_TYPES = frozenset({
    "function_declaration", "class_declaration",
    "interface_declaration", "lexical_declaration",
})


def extract_typescript_entities(
    root: Node, file_path: str, calls_query: Query = _TS_CALLS_QUERY,
) -> Iterator[tuple[Entity, FeatureSet]]:
    """Walk top-level siblings, accumulating ``decorator`` nodes until
    each named item, then emit it with those decorators attached.

    ``calls_query`` is the per-grammar compiled tree-sitter Query for
    matching call expressions (TS or TSX — see module-level
    ``_TS_CALLS_QUERY`` / ``_TSX_CALLS_QUERY``). Threaded through the
    emit chain because Query is bound to a Language and a Node has
    no back-pointer to its tree.
    """
    yield from _walk_with_decorators(root.children, file_path, calls_query)


def _walk_with_decorators(
    children: Iterable[Node], file_path: str, calls_query: Query,
) -> Iterator[tuple[Entity, FeatureSet]]:
    pending: list[str] = []
    for node in children:
        if node.type == "decorator":
            name = _decorator_name(node)
            if name:
                pending.append(name)
            continue
        if node.type == "export_statement":
            # Decorators may live inside the export_statement too; fold them in.
            yield from _walk_with_decorators(
                _flatten_export(node, pending), file_path, calls_query,
            )
            pending = []
            continue
        if node.type in _ITEM_TYPES:
            yield from _emit_item(node, file_path, tuple(pending), calls_query)
            pending = []
            continue
        # Anything else resets pending decorators (they don't carry across).
        pending = []


def _flatten_export(
    export_node: Node, outer_pending: list[str]
) -> Iterator[Node]:
    """Yield children of an export_statement so they look like top-level
    siblings.

    ``outer_pending`` holds decorator names already collected *outside*
    the export_statement (rare — usually decorators live inside it).
    We can't fabricate Node objects for them, but ``_inline_decorators``
    on the inner item picks up its own children, and the typical case
    (``@dec export class Foo {}`` parses with the decorator inside the
    export_statement) works without fabrication.
    """
    del outer_pending  # documented above; not consumed in this pass
    for child in export_node.children:
        if child.type == "export":
            continue
        yield child


def _emit_item(
    node: Node, file_path: str, decorators: tuple[str, ...],
    calls_query: Query,
) -> Iterator[tuple[Entity, FeatureSet]]:
    if node.type == "function_declaration":
        yield _emit_function(node, file_path, decorators, calls_query)
    elif node.type == "class_declaration":
        yield from _emit_class(node, file_path, decorators, calls_query)
    elif node.type == "interface_declaration":
        yield from _emit_interface(node, file_path, decorators)
    elif node.type == "lexical_declaration":
        for declarator in node.children:
            if declarator.type == "variable_declarator":
                emitted = _from_declarator(declarator, file_path, calls_query)
                if emitted is not None:
                    yield emitted


def _from_declarator(
    declarator: Node, file_path: str, calls_query: Query,
) -> tuple[Entity, FeatureSet] | None:
    name_node = declarator.child_by_field_name("name")
    value_node = declarator.child_by_field_name("value")
    if name_node is None or value_node is None:
        return None
    if value_node.type not in ("arrow_function", "function_expression"):
        return None
    name = name_node.text.decode("utf-8")
    entity = Entity(
        kind="function",
        qualified_name=f"{file_path}::{name}",
        file_path=file_path,
        line=value_node.start_point[0] + 1,
    )
    features = FeatureSet(by_kind={
        "decorator": frozenset(),
        "calls": frozenset(_walk_calls(value_node, calls_query)),
    })
    return entity, features


def _emit_function(
    fn_node: Node, file_path: str, decorators: tuple[str, ...],
    calls_query: Query,
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
        "decorator": frozenset(decorators) | _inline_decorators(fn_node),
        "calls": frozenset(_walk_calls(fn_node, calls_query)),
    })
    return entity, features


def _emit_class(
    class_node: Node, file_path: str, decorators: tuple[str, ...],
    calls_query: Query,
) -> Iterator[tuple[Entity, FeatureSet]]:
    name_node = class_node.child_by_field_name("name")
    name = name_node.text.decode("utf-8") if name_node else "<anonymous>"
    parents = frozenset(_class_supertypes(class_node))

    yield (
        Entity(
            kind="class",
            qualified_name=f"{file_path}::{name}",
            file_path=file_path,
            line=class_node.start_point[0] + 1,
        ),
        FeatureSet(by_kind={
            "decorator": frozenset(decorators) | _inline_decorators(class_node),
            "parent_class": parents,
        }),
    )

    body = class_node.child_by_field_name("body")
    if body is None:
        return
    yield from _walk_class_members(body.children, file_path, name, calls_query)


def _walk_class_members(
    children: Iterable[Node], file_path: str, class_name: str,
    calls_query: Query,
) -> Iterator[tuple[Entity, FeatureSet]]:
    pending: list[str] = []
    for node in children:
        if node.type == "decorator":
            name = _decorator_name(node)
            if name:
                pending.append(name)
            continue
        if node.type == "method_definition":
            yield _emit_method(
                node, file_path, class_name, tuple(pending), calls_query,
            )
            pending = []
            continue
        # Properties, accessors, etc. — reset decorators
        pending = []


def _emit_method(
    method_node: Node, file_path: str, class_name: str,
    decorators: tuple[str, ...], calls_query: Query,
) -> tuple[Entity, FeatureSet]:
    name_node = method_node.child_by_field_name("name")
    name = name_node.text.decode("utf-8") if name_node else "<anonymous>"
    entity = Entity(
        kind="method",
        qualified_name=f"{file_path}::{class_name}.{name}",
        file_path=file_path,
        line=method_node.start_point[0] + 1,
    )
    features = FeatureSet(by_kind={
        "decorator": frozenset(decorators) | _inline_decorators(method_node),
        "calls": frozenset(_walk_calls(method_node, calls_query)),
    })
    return entity, features


def _emit_interface(
    iface_node: Node, file_path: str, decorators: tuple[str, ...]
) -> Iterator[tuple[Entity, FeatureSet]]:
    name_node = iface_node.child_by_field_name("name")
    if name_node is None:
        return
    name = name_node.text.decode("utf-8")
    parents = frozenset(_interface_supertypes(iface_node))
    yield (
        Entity(
            kind="interface",
            qualified_name=f"{file_path}::{name}",
            file_path=file_path,
            line=iface_node.start_point[0] + 1,
        ),
        FeatureSet(by_kind={
            "decorator": frozenset(decorators) | _inline_decorators(iface_node),
            "parent_class": parents,
        }),
    )


def _inline_decorators(node: Node) -> frozenset[str]:
    """Collect decorator names that live as direct children of a definition
    node. TypeScript stores `@dec class Foo {}` with the decorator inside
    the class_declaration when not wrapped in an export_statement; the
    sibling-walking pass alone misses these."""
    out: list[str] = []
    for child in node.children:
        if child.type == "decorator":
            name = _decorator_name(child)
            if name:
                out.append(name)
    return frozenset(out)


# ── Helpers ──────────────────────────────────────────────────────────


def _class_supertypes(class_node: Node) -> Iterator[str]:
    """Yield every supertype this class extends or implements.

    ``class_heritage`` may contain an ``extends_clause`` and zero or
    more ``implements_clause``s. We capture all of them as
    ``parent_class``."""
    for child in class_node.children:
        if child.type != "class_heritage":
            continue
        for sub in child.children:
            if sub.type == "extends_clause":
                yield from _names_in(sub)
            elif sub.type == "implements_clause":
                yield from _names_in(sub)


def _interface_supertypes(iface_node: Node) -> Iterator[str]:
    """Interfaces can extend other interfaces via an ``extends_clause``."""
    for child in iface_node.children:
        if child.type == "extends_type_clause":
            yield from _names_in(child)
        elif child.type == "extends_clause":
            yield from _names_in(child)


def _names_in(clause: Node) -> Iterator[str]:
    """Yield every identifier / member-expression / type-identifier name
    inside an extends/implements clause, skipping keywords and punctuation."""
    for child in clause.children:
        if child.type in ("identifier", "type_identifier", "member_expression"):
            yield child.text.decode("utf-8").strip()
        elif child.type == "generic_type":
            # Generic-typed reference, e.g. `Foo<Bar>` — take the leftmost type
            for sub in child.children:
                if sub.type in ("type_identifier", "identifier", "member_expression"):
                    yield sub.text.decode("utf-8").strip()
                    break


def _decorator_name(decorator_node: Node) -> str | None:
    """Canonical decorator name (e.g. ``@injectable``).

    A decorator is ``@`` followed by a call_expression or an identifier.
    We want just the callable name with any ``(args)`` dropped — same
    convention as Python's decorators.
    """
    text = decorator_node.text.decode("utf-8").strip()
    bare = text.lstrip("@").split("(")[0].strip()
    return "@" + bare if bare else None


def _walk_calls(root: Node, calls_query: Query) -> Iterator[str]:
    cursor = QueryCursor(calls_query)
    for _, captures in cursor.matches(root):
        for target in captures.get("target", ()):
            yield clean_call_name(target.text.decode("utf-8").strip())

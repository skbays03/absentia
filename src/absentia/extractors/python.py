"""Python extractor.

Extracts top-level functions, top-level classes, and methods inside
those classes. Nested functions, async functions inside classes, and
classes-inside-functions are out of scope for the MVP.

Feature kinds emitted:

  - functions/methods: ``decorator``, ``calls``
  - classes:           ``decorator``, ``parent_class``
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import ClassVar

import tree_sitter_python
from tree_sitter import Language, Node, Parser, Query, QueryCursor

from ..entities import Entity, FeatureSet, clean_call_name
from .base import Extractor


_PY_LANGUAGE = Language(tree_sitter_python.language())

# Tree-sitter Query API: matches every Python ``call`` node in a
# subtree, capturing the call's ``function`` field as @target.
# Compiled once at import; runs in C, much faster than the previous
# Python ``walk_subtree`` + ``if node.type ==`` loop.
_CALLS_QUERY = Query(_PY_LANGUAGE, "(call function: (_) @target)")


class PythonExtractor(Extractor):
    language_name: ClassVar[str] = "python"
    file_extensions: ClassVar[tuple[str, ...]] = (".py", ".pyw")

    def __init__(self) -> None:
        self._parser = Parser(_PY_LANGUAGE)

    def parse(self, source: bytes) -> Node:
        return self._parser.parse(source).root_node

    def extract(
        self, root: Node, file_path: str
    ) -> Iterable[tuple[Entity, FeatureSet]]:
        return extract_python_entities(root, file_path)


# ── Module-level extraction (used directly in tests + by the class) ────


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
        # has_docstring is populated for every function so mining sees
        # the function as eligible; the value "docstring" is present
        # iff the function actually has one. Mining reads "X% of group
        # members have value 'docstring'; this one doesn't" → emits
        # gap "missing docstring".
        "has_docstring": _docstring_marker(fn_node),
        # Type-annotation features. Same shape: always populated so
        # the function is eligible; value present iff the annotation
        # is. Renders as "missing return type" / "missing param types".
        "has_return_type": _return_type_marker(fn_node),
        "has_param_types": _param_types_marker(fn_node),
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
            "has_docstring": _docstring_marker(class_node),
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
        "has_docstring": _docstring_marker(fn_node),
        "has_return_type": _return_type_marker(fn_node),
        "has_param_types": _param_types_marker(fn_node),
    })
    return entity, features


# ── Helpers ──────────────────────────────────────────────────────────


def _name_of(definition_node: Node) -> str:
    name_node = definition_node.child_by_field_name("name")
    return name_node.text.decode("utf-8") if name_node else "<anonymous>"


def _walk_calls(root: Node) -> Iterator[str]:
    cursor = QueryCursor(_CALLS_QUERY)
    for _, captures in cursor.matches(root):
        for target in captures.get("target", ()):
            yield clean_call_name(target.text.decode("utf-8").strip())


def _return_type_marker(fn_node: Node) -> frozenset[str]:
    """``frozenset({"return type"})`` iff the function has a ``-> X:``
    annotation, else ``frozenset()``. Mining surfaces gaps as
    "missing return type".
    """
    return (
        frozenset({"return type"})
        if fn_node.child_by_field_name("return_type") is not None
        else frozenset()
    )


def _param_types_marker(fn_node: Node) -> frozenset[str]:
    """``frozenset({"param types"})`` iff every non-self / non-cls
    positional parameter carries a type annotation. Empty param list
    counts as annotated (nothing to be missing). Conservative: any
    untyped parameter flips the whole function to "missing param
    types" — matches how teams adopt typing (gradually, but with the
    expectation that a well-typed function annotates everything).
    """
    params = fn_node.child_by_field_name("parameters")
    if params is None:
        return frozenset({"param types"})
    typed_kinds = {"typed_parameter", "typed_default_parameter"}
    untyped_kinds = {"identifier", "default_parameter"}
    for child in params.children:
        if child.type in untyped_kinds:
            text = child.text.decode("utf-8").strip()
            # Drop default-value suffix ("foo=1" → "foo") before
            # checking self/cls.
            name = text.split("=", 1)[0].strip()
            if name in ("self", "cls"):
                continue
            return frozenset()
        if child.type in typed_kinds:
            continue
        # Non-parameter children: parens, commas, *args markers.
        # Skip silently.
    return frozenset({"param types"})


def _docstring_marker(definition_node: Node) -> frozenset[str]:
    """Return ``frozenset({"docstring"})`` if the def has a non-empty
    docstring, else ``frozenset()``. Empty/missing docstring → eligible
    for the missing-docstring gap; populated → contributes to the
    "X% of group has docstring" denominator.

    A Python docstring is the first statement of the body, an
    ``expression_statement`` whose only child is a ``string``. We
    require at least one non-quote character — pure ``""""""``
    placeholders shouldn't count.
    """
    body = definition_node.child_by_field_name("body")
    if body is None:
        return frozenset()
    for child in body.children:
        if child.type == "comment":
            continue  # leading comments don't disqualify a docstring
        if child.type != "expression_statement":
            return frozenset()
        for sub in child.children:
            if sub.type != "string":
                return frozenset()
            text = sub.text.decode("utf-8")
            # Strip the quote pairs (`"""..."""`, `"..."`, `'...'`)
            # and check there's any non-whitespace content left.
            stripped = text.strip().strip('"').strip("'").strip()
            return frozenset({"docstring"}) if stripped else frozenset()
        return frozenset()
    return frozenset()


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

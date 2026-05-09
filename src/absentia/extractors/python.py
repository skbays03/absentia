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
# Captures every keyword argument name appearing in any call within
# a subtree. Used by call_kwargs mining (Item C) to surface "every
# endpoint passes request_id= to some call; this one doesn't."
_KWARGS_QUERY = Query(
    _PY_LANGUAGE,
    "(keyword_argument name: (identifier) @kwarg_name)",
)


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
    """Yield (entity, features) for top-level functions, classes, the
    methods inside those classes, plus a single ``module`` entity per
    file carrying module-scope features (``has_all_export``)."""
    yield _emit_module(root, file_path)
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


def _emit_module(
    root: Node,
    file_path: str,
) -> tuple[Entity, FeatureSet]:
    """One module entity per .py file. Carries module-scope features
    that don't fit on any single function or class — currently just
    ``has_all_export`` (whether the file declares ``__all__`` at
    module scope). Mining over the directory selector treats the
    module as just another group member; eligibility filtering in
    mine() means modules don't pollute decorator/parent_class rules."""
    return (
        Entity(
            kind="module",
            qualified_name=f"{file_path}::__module__",
            file_path=file_path,
            line=1,
        ),
        FeatureSet(by_kind={
            "has_all_export": _all_export_marker(root),
        }),
    )


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
        # call_kwargs is the set of keyword-argument names appearing
        # in any call inside this function's body, with a trailing
        # `=` so output reads "missing request_id=". Surfaces logging
        # / tracing conventions: "every endpoint passes request_id=
        # to some call; this one doesn't."
        "call_kwargs": frozenset(_walk_call_kwargs(fn_node)),
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
            # Always populated; value present iff the class defines a
            # __post_init__ method. Renders as "missing __post_init__"
            # when most siblings in the same group have one — typical
            # signal for config dataclasses where validation is the
            # convention.
            "has_post_init": _post_init_marker(class_node),
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
        "call_kwargs": frozenset(_walk_call_kwargs(fn_node)),
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


def _walk_call_kwargs(root: Node) -> Iterator[str]:
    """Yield each ``name=`` token used as a keyword argument anywhere
    in ``root``'s call subtree. Returns the bare identifier with a
    trailing ``=`` so gap output reads "missing request_id=" — visually
    obvious that we're talking about a kwarg, not a free identifier."""
    cursor = QueryCursor(_KWARGS_QUERY)
    for _, captures in cursor.matches(root):
        for name in captures.get("kwarg_name", ()):
            yield f"{name.text.decode('utf-8').strip()}="


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


def _all_export_marker(root: Node) -> frozenset[str]:
    """Return ``frozenset({"__all__"})`` if the module declares
    ``__all__`` at module scope, else ``frozenset()``. We accept any
    assignment whose left-hand side is the bare identifier ``__all__``;
    we don't validate the RHS shape (``[...]`` vs ``(...)`` vs
    ``foo + bar``) — declaring it at all is the convention.
    """
    for child in root.children:
        # Plain `__all__ = [...]` is an `expression_statement` whose
        # only child is an `assignment`.
        if child.type == "expression_statement":
            for sub in child.children:
                if sub.type == "assignment":
                    target = sub.child_by_field_name("left")
                    if (
                        target is not None
                        and target.type == "identifier"
                        and target.text == b"__all__"
                    ):
                        return frozenset({"__all__"})
    return frozenset()


def _post_init_marker(class_node: Node) -> frozenset[str]:
    """Return ``frozenset({"__post_init__"})`` if the class defines a
    ``__post_init__`` method, else ``frozenset()``. Mining reads this as
    "X% of the group has __post_init__; this one doesn't" → emits the
    "missing __post_init__" gap. Useful in directories where config
    dataclasses validate themselves on construction by convention.
    """
    body = class_node.child_by_field_name("body")
    if body is None:
        return frozenset()
    for member in body.children:
        # Plain method definition.
        if member.type == "function_definition":
            if _name_of(member) == "__post_init__":
                return frozenset({"__post_init__"})
        # Decorated method (@staticmethod, @abstractmethod, etc.) —
        # walk in to the inner function_definition.
        elif member.type == "decorated_definition":
            for child in member.children:
                if (
                    child.type == "function_definition"
                    and _name_of(child) == "__post_init__"
                ):
                    return frozenset({"__post_init__"})
    return frozenset()


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

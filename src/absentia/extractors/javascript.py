"""JavaScript extractor.

Top-level functions (declared, arrow, function expression), classes,
and methods inside classes. Class inheritance via ``extends`` produces
a ``parent_class`` feature.

Feature kinds emitted:
  - functions/methods: ``calls``
  - classes:           ``parent_class``

Decorators are not extracted here. TC39 decorators are stage-3 and
extremely rare in plain JS; the (eventual) TypeScript extractor
handles the common TS decorator pattern.
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import ClassVar

import tree_sitter_javascript
from tree_sitter import Language, Node, Parser, Query, QueryCursor

from ..entities import Entity, FeatureSet, clean_call_name
from .base import Extractor


_JS_LANGUAGE = Language(tree_sitter_javascript.language())
_CALLS_QUERY = Query(_JS_LANGUAGE, "(call_expression function: (_) @target)")


class JavaScriptExtractor(Extractor):
    language_name: ClassVar[str] = "javascript"
    file_extensions: ClassVar[tuple[str, ...]] = (".js", ".jsx", ".mjs", ".cjs")

    def __init__(self) -> None:
        self._parser = Parser(_JS_LANGUAGE)

    def parse(self, source: bytes) -> Node:
        return self._parser.parse(source).root_node

    def extract(
        self, root: Node, file_path: str
    ) -> Iterable[tuple[Entity, FeatureSet]]:
        return extract_javascript_entities(root, file_path)


def extract_javascript_entities(
    root: Node, file_path: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    for child in root.children:
        yield from _process_top_level(child, file_path)


def _process_top_level(
    node: Node, file_path: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    if node.type == "function_declaration":
        yield _emit_function(node, file_path)
    elif node.type == "class_declaration":
        yield from _emit_class(node, file_path)
    elif node.type == "lexical_declaration":
        # const/let/var declarations may bind arrow functions or function
        # expressions; treat those as named functions. They may also bind
        # an IIFE (revealing-module pattern) — descend into the IIFE's
        # body so encapsulated functions are visible to mining.
        for declarator in node.children:
            if declarator.type == "variable_declarator":
                yield from _from_declarator(declarator, file_path)
    elif node.type == "expression_statement":
        # Top-level bare IIFE: `(function () { ... })();`
        # Walk into it the same way named-IIFE binding does.
        for child in node.children:
            iife_body = _iife_body(child)
            if iife_body is not None:
                yield from _walk_iife_body(iife_body, file_path, namespace="")


def _from_declarator(
    declarator: Node, file_path: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    """Emit entities for a `const foo = ...` declarator.

    Three forms are recognized:

    1. Arrow / function expression: `const foo = () => {...}` — emit
       a single function entity.
    2. IIFE (revealing-module pattern): `const App = (() => {...})()` —
       descend into the IIFE body and emit each declared function /
       class with `App.` prefixed onto its qualified name. This is
       how the bulk of pre-ES-modules JS encapsulates module state;
       not handling it leaves a 95%+ entity-extraction gap on
       affected codebases.
    3. Class expression: `const Foo = class {...}` — not yet handled
       (rare); falls through silently.
    """
    name_node = declarator.child_by_field_name("name")
    value_node = declarator.child_by_field_name("value")
    if name_node is None or value_node is None:
        return
    name = name_node.text.decode("utf-8")

    # Form 2: IIFE with this declarator's name as the namespace.
    iife_body = _iife_body(value_node)
    if iife_body is not None:
        yield from _walk_iife_body(iife_body, file_path, namespace=name)
        return

    # Form 1: direct arrow / function expression binding.
    if value_node.type not in ("arrow_function", "function_expression"):
        return
    entity = Entity(
        kind="function",
        qualified_name=f"{file_path}::{name}",
        file_path=file_path,
        line=value_node.start_point[0] + 1,
    )
    features = FeatureSet(by_kind={
        "calls": frozenset(_walk_calls(value_node)),
    })
    yield entity, features


def _iife_body(node: Node) -> Node | None:
    """Return the inner function body if ``node`` is an IIFE, else None.

    Recognizes the two common shapes:

      ``(() => { ... })()``                  — paren-wrapped arrow + call
      ``(function () { ... })()``            — paren-wrapped function expr
      ``(function name () { ... })()``       — named function expr in same shape

    Tree-sitter's parse: ``call_expression`` whose ``function`` field
    is a ``parenthesized_expression`` containing either an
    ``arrow_function`` or ``function_expression``. Returns the inner
    function's body node (a ``statement_block``) for the caller to
    walk; returns ``None`` for any non-IIFE shape."""
    if node.type != "call_expression":
        return None
    fn_field = node.child_by_field_name("function")
    if fn_field is None:
        return None
    inner = fn_field
    # The function field may itself be a parenthesized_expression
    # wrapping the actual arrow / function expression.
    if fn_field.type == "parenthesized_expression":
        for child in fn_field.children:
            if child.type in ("arrow_function", "function_expression"):
                inner = child
                break
        else:
            return None
    if inner.type not in ("arrow_function", "function_expression"):
        return None
    body = inner.child_by_field_name("body")
    if body is None or body.type != "statement_block":
        return None
    return body


def _walk_iife_body(
    body: Node, file_path: str, namespace: str,
) -> Iterator[tuple[Entity, FeatureSet]]:
    """Extract function and class declarations from inside an IIFE
    statement block.

    ``namespace`` is prefixed to each emitted entity's qualified name
    (e.g. ``App.init``) so encapsulated functions don't collide with
    same-named functions in other IIFEs across the corpus. Pass
    ``""`` for top-level bare IIFEs that don't have a binding name."""
    prefix = f"{namespace}." if namespace else ""
    for stmt in body.children:
        if stmt.type == "function_declaration":
            name_node = stmt.child_by_field_name("name")
            if name_node is None:
                continue
            inner_name = name_node.text.decode("utf-8")
            entity = Entity(
                kind="function",
                qualified_name=f"{file_path}::{prefix}{inner_name}",
                file_path=file_path,
                line=stmt.start_point[0] + 1,
            )
            features = FeatureSet(by_kind={
                "calls": frozenset(_walk_calls(stmt)),
            })
            yield entity, features
        elif stmt.type == "class_declaration":
            yield from _emit_class(stmt, file_path)
        elif stmt.type == "lexical_declaration":
            # Nested `const inner = () => {...}` inside the IIFE.
            for d in stmt.children:
                if d.type == "variable_declarator":
                    name_n = d.child_by_field_name("name")
                    val_n = d.child_by_field_name("value")
                    if (
                        name_n is not None
                        and val_n is not None
                        and val_n.type in (
                            "arrow_function", "function_expression",
                        )
                    ):
                        inner_name = name_n.text.decode("utf-8")
                        entity = Entity(
                            kind="function",
                            qualified_name=(
                                f"{file_path}::{prefix}{inner_name}"
                            ),
                            file_path=file_path,
                            line=val_n.start_point[0] + 1,
                        )
                        features = FeatureSet(by_kind={
                            "calls": frozenset(_walk_calls(val_n)),
                        })
                        yield entity, features


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


def _emit_class(
    class_node: Node, file_path: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    name_node = class_node.child_by_field_name("name")
    name = name_node.text.decode("utf-8") if name_node else "<anonymous>"
    parents = frozenset(_extends_of(class_node))

    yield (
        Entity(
            kind="class",
            qualified_name=f"{file_path}::{name}",
            file_path=file_path,
            line=class_node.start_point[0] + 1,
        ),
        FeatureSet(by_kind={
            "parent_class": parents,
        }),
    )

    body = class_node.child_by_field_name("body")
    if body is None:
        return
    for member in body.children:
        if member.type == "method_definition":
            yield _emit_method(member, file_path, name)


def _emit_method(
    method_node: Node, file_path: str, class_name: str
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
        "calls": frozenset(_walk_calls(method_node)),
    })
    return entity, features


# ── Helpers ──────────────────────────────────────────────────────────


def _extends_of(class_node: Node) -> Iterator[str]:
    """Yield the names of classes the given class extends.

    JS allows only one parent class, but it can be a dotted member
    expression like ``foo.Base``. We treat the full text as the name.
    """
    for child in class_node.children:
        if child.type == "class_heritage":
            for sub in child.children:
                if sub.type in ("identifier", "member_expression"):
                    yield sub.text.decode("utf-8").strip()


def _walk_calls(root: Node) -> Iterator[str]:
    cursor = QueryCursor(_CALLS_QUERY)
    for _, captures in cursor.matches(root):
        for target in captures.get("target", ()):
            yield clean_call_name(target.text.decode("utf-8").strip())

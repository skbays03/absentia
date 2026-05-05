"""Ruby extractor.

Top-level classes, modules, and methods inside their bodies. Ruby has
no annotations / decorators, so the ``decorator`` feature is not
emitted. Inheritance via ``class Foo < Bar`` and mixin via ``include
Bar`` / ``extend Bar`` / ``prepend Bar`` both feed ``parent_class``
(treating "is_a"-style relationships uniformly, same approach as
Swift / TS).

Feature kinds emitted:
  - methods:                  ``calls``
  - classes/modules:          ``parent_class``
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import ClassVar

import tree_sitter_ruby
from tree_sitter import Language, Node, Parser

from ..entities import Entity, FeatureSet, clean_call_name
from .base import Extractor


_RUBY_LANGUAGE = Language(tree_sitter_ruby.language())

_MIXIN_KEYWORDS = frozenset({"include", "extend", "prepend"})


class RubyExtractor(Extractor):
    language_name: ClassVar[str] = "ruby"
    file_extensions: ClassVar[tuple[str, ...]] = (".rb",)

    def __init__(self) -> None:
        self._parser = Parser(_RUBY_LANGUAGE)

    def parse(self, source: bytes) -> Node:
        return self._parser.parse(source).root_node

    def extract(
        self, root: Node, file_path: str
    ) -> Iterable[tuple[Entity, FeatureSet]]:
        return extract_ruby_entities(root, file_path)


def extract_ruby_entities(
    root: Node, file_path: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    for child in root.children:
        if child.type == "class":
            yield from _emit_class(child, file_path)
        elif child.type == "module":
            yield from _emit_module(child, file_path)


def _emit_class(
    class_node: Node, file_path: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    name = _name_of(class_node)
    if name is None:
        return
    superclass = _superclass_of(class_node)
    body = _find_child(class_node, "body_statement")
    mixins = _mixins_in(body) if body is not None else frozenset()

    parents = mixins
    if superclass is not None:
        parents = frozenset({superclass}) | mixins

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

    if body is not None:
        yield from _emit_methods(body, file_path, name)


def _emit_module(
    module_node: Node, file_path: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    name = _name_of(module_node)
    if name is None:
        return
    body = _find_child(module_node, "body_statement")
    mixins = _mixins_in(body) if body is not None else frozenset()

    yield (
        Entity(
            kind="module",
            qualified_name=f"{file_path}::{name}",
            file_path=file_path,
            line=module_node.start_point[0] + 1,
        ),
        FeatureSet(by_kind={
            "parent_class": mixins,
        }),
    )

    if body is not None:
        yield from _emit_methods(body, file_path, name)


def _emit_methods(
    body: Node, file_path: str, container_name: str
) -> Iterator[tuple[Entity, FeatureSet]]:
    for child in body.children:
        if child.type == "method":
            yield _emit_method(child, file_path, container_name)


def _emit_method(
    method_node: Node, file_path: str, container_name: str
) -> tuple[Entity, FeatureSet]:
    name = _method_name(method_node)
    entity = Entity(
        kind="method",
        qualified_name=f"{file_path}::{container_name}.{name}",
        file_path=file_path,
        line=method_node.start_point[0] + 1,
    )
    features = FeatureSet(by_kind={
        "calls": frozenset(_walk_calls(method_node)),
    })
    return entity, features


# ── Helpers ──────────────────────────────────────────────────────────


def _name_of(node: Node) -> str | None:
    """Class/module names are ``constant`` children."""
    for child in node.children:
        if child.type == "constant":
            return child.text.decode("utf-8")
    return None


def _method_name(method_node: Node) -> str:
    for child in method_node.children:
        if child.type == "identifier":
            return child.text.decode("utf-8")
    return "<anonymous>"


def _find_child(node: Node, child_type: str) -> Node | None:
    for child in node.children:
        if child.type == child_type:
            return child
    return None


def _superclass_of(class_node: Node) -> str | None:
    sup = _find_child(class_node, "superclass")
    if sup is None:
        return None
    for child in sup.children:
        if child.type == "constant":
            return child.text.decode("utf-8")
        if child.type == "scope_resolution":
            return child.text.decode("utf-8")
    return None


def _mixins_in(body: Node) -> frozenset[str]:
    """Walk a class/module body for ``include``/``extend``/``prepend`` calls
    and return the set of constants they pull in."""
    out: list[str] = []
    for child in body.children:
        if child.type != "call":
            continue
        # Must be a bare-identifier call (not a method invocation on a receiver)
        first = child.children[0] if child.children else None
        if first is None or first.type != "identifier":
            continue
        if first.text.decode("utf-8") not in _MIXIN_KEYWORDS:
            continue
        # Pull each constant out of the argument_list
        args = _find_child(child, "argument_list")
        if args is None:
            continue
        for arg in args.children:
            if arg.type == "constant":
                out.append(arg.text.decode("utf-8"))
            elif arg.type == "scope_resolution":
                out.append(arg.text.decode("utf-8"))
    return frozenset(out)


def _walk_calls(node: Node) -> Iterator[str]:
    """Ruby's ``call`` node either:

    - Starts with an ``identifier`` (bare call: ``helper(x)``)
    - Starts with a receiver (``self``, ``constant``, expression) then
      ``.`` then ``identifier`` (``self.update``, ``Logger.info``)

    We yield the textual name. Bare identifiers without parens (which
    Ruby allows for method calls but is ambiguous with local variables)
    are NOT counted — only explicit ``call`` nodes.
    """
    for child in node.children:
        if child.type == "call":
            yield clean_call_name(_call_name(child))
        yield from _walk_calls(child)


def _call_name(call_node: Node) -> str:
    """Reconstruct the textual name of a Ruby call expression.

    For ``self.update`` we want ``"self.update"``; for ``foo&.bar`` we
    normalize the safe-navigation operator to a regular dot. We skip
    the opening paren and bail at the argument_list so ``helper(x)``
    becomes just ``"helper"``.
    """
    parts: list[str] = []
    for child in call_node.children:
        if child.type == "argument_list":
            break
        if child.type == "(":
            continue
        if child.type == "&.":
            parts.append(".")
            continue
        parts.append(child.text.decode("utf-8"))
    return "".join(parts).strip()

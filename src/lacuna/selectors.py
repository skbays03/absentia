"""Selectors — group entities into mining cohorts.

A Selector is conceptually `entities → list[Group]`. Each emits zero or
more groups; an entity can be in many groups simultaneously, and rules
apply independently per group.

MVP: only the directory selector is implemented. Decorator, parent_class,
name_pattern, cluster, and manual selectors land in subsequent passes.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable

from .entities import Entity, FeatureSet


@dataclass(frozen=True)
class Group:
    name: str                       # human-readable: "src/api" or "@audit"
    selector_type: str              # "directory" | "decorator" | ...
    members: tuple[str, ...]        # entity ids

    @property
    def id(self) -> str:
        return f"{self.selector_type}::{self.name}"

    @property
    def identity_feature(self) -> tuple[str, str] | None:
        """The (feature_kind, value) pair this group is defined by, if any.

        Mining skips rules whose predicate matches this — they would be
        trivially true by construction (a group of @audit-decorated fns
        unsurprisingly has 100% @audit decoration) and produce no gaps.
        Returns None for selectors not defined by a single feature value
        (e.g. directory)."""
        if self.selector_type == "decorator":
            return ("decorator", self.name)
        if self.selector_type == "parent_class":
            return ("parent_class", self.name)
        return None


def directory_groups(
    items: Iterable[tuple[Entity, FeatureSet]],
    *,
    min_members: int = 3,
    kind_filter: tuple[str, ...] = ("function", "class"),
) -> list[Group]:
    """Group entities by their immediate parent directory."""
    by_dir: dict[str, list[str]] = defaultdict(list)
    for entity, _features in items:
        if entity.kind not in kind_filter:
            continue
        parent = str(PurePosixPath(entity.file_path).parent)
        if parent in (".", ""):
            parent = "<root>"
        by_dir[parent].append(entity.id)
    return [
        Group(name=name, selector_type="directory", members=tuple(ids))
        for name, ids in sorted(by_dir.items())
        if len(ids) >= min_members
    ]


_DEFAULT_DECORATOR_EXCLUDES: tuple[str, ...] = (
    "@property", "@staticmethod", "@classmethod",
)

_DEFAULT_PARENT_CLASS_EXCLUDES: tuple[str, ...] = (
    "object",  # universal Python superclass; not a useful grouping
)

# Entity kinds that can carry a parent_class feature. Includes class for
# Python/JS class inheritance and the Swift family (struct/enum/extension/
# protocol all participate in protocol conformance, which we model as the
# same feature kind).
_DEFAULT_PARENT_CLASS_KINDS: tuple[str, ...] = (
    "class", "struct", "enum", "extension", "protocol",
)


def parent_class_groups(
    items: Iterable[tuple[Entity, FeatureSet]],
    *,
    min_members: int = 3,
    exclude: tuple[str, ...] = _DEFAULT_PARENT_CLASS_EXCLUDES,
    kind_filter: tuple[str, ...] = _DEFAULT_PARENT_CLASS_KINDS,
) -> list[Group]:
    """One group per unique parent class / conformed protocol. Members are
    type-defining entities (classes, structs, enums, extensions, protocols)
    whose ``parent_class`` feature contains that name. An entity with
    multiple parents is a member of every corresponding group, enabling
    co-occurrence rules across mixins / protocol conformances."""
    by_parent: dict[str, list[str]] = defaultdict(list)
    excluded = frozenset(exclude)
    eligible_kinds = frozenset(kind_filter)
    for entity, features in items:
        if entity.kind not in eligible_kinds:
            continue
        for parent in features.get_set("parent_class"):
            if parent in excluded:
                continue
            by_parent[parent].append(entity.id)
    return [
        Group(name=name, selector_type="parent_class", members=tuple(ids))
        for name, ids in sorted(by_parent.items())
        if len(ids) >= min_members
    ]


def decorator_groups(
    items: Iterable[tuple[Entity, FeatureSet]],
    *,
    min_members: int = 3,
    exclude: tuple[str, ...] = _DEFAULT_DECORATOR_EXCLUDES,
) -> list[Group]:
    """One group per unique decorator. An entity carrying multiple
    decorators is a member of every corresponding group, which is what
    enables co-occurrence rules (e.g. ``@app.route`` handlers usually
    also have ``@audit``)."""
    by_decorator: dict[str, list[str]] = defaultdict(list)
    excluded = frozenset(exclude)
    for entity, features in items:
        for dec in features.get_set("decorator"):
            if dec in excluded:
                continue
            by_decorator[dec].append(entity.id)
    return [
        Group(name=name, selector_type="decorator", members=tuple(ids))
        for name, ids in sorted(by_decorator.items())
        if len(ids) >= min_members
    ]

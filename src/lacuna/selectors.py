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
    name: str                       # human-readable: "src/api" or "<root>"
    selector_type: str              # "directory" for now
    members: tuple[str, ...]        # entity ids

    @property
    def id(self) -> str:
        return f"{self.selector_type}::{self.name}"


def directory_groups(
    items: Iterable[tuple[Entity, FeatureSet]],
    *,
    min_members: int = 3,
    kind_filter: tuple[str, ...] = ("function",),
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

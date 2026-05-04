"""Entity model — the things lacuna mines.

For the MVP, entities are top-level Python functions. Future extractors
will add classes, methods, files, imports, and decorator-uses.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Entity:
    kind: str                    # "function" | "class" | "method" | ...
    qualified_name: str          # e.g. "src/api/users.py::delete_user"
    file_path: str               # POSIX, relative to corpus root
    line: int                    # 1-indexed source line

    @property
    def id(self) -> str:
        # MVP: qualified_name is unique and human-readable. A future
        # iteration can switch to a deterministic short hash for compactness.
        return self.qualified_name


@dataclass
class FeatureSet:
    """Features an entity exhibits. Kind-keyed; values are JSON-encodable."""
    by_kind: dict[str, Any] = field(default_factory=dict)

    def get_set(self, kind: str) -> frozenset[str]:
        value = self.by_kind.get(kind)
        if value is None:
            return frozenset()
        return frozenset(value)

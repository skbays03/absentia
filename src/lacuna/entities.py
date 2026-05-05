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


def clean_call_name(text: str) -> str:
    """Collapse the first parenthesized run in a call expression's textual
    name to ``(...)`` so chained calls stay short.

    ``parse_low_raw(None::<&str>).unwrap`` → ``parse_low_raw(...).unwrap``
    ``foo``                                  → ``foo``  (no change)
    ``self.update``                          → ``self.update``  (no parens)

    Used by every extractor that yields call names — keeps mining stable
    (the same chained pattern still produces the same feature value) while
    making output readable when receivers are themselves calls with
    complex argument lists.
    """
    if "(" not in text:
        return text
    open_idx = text.index("(")
    depth = 0
    for i in range(open_idx, len(text)):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[:open_idx] + "(...)" + text[i + 1:]
    return text

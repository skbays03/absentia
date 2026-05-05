"""Symmetry pair detection — the second mining strategy.

Frequency mining (the original engine) catches "most do X; this
doesn't." Symmetry catches a different shape of gap: **entities
that have a "left" without a corresponding "right"**, where the
relationship is structural rather than statistical. Examples:

  - A class with ``__enter__`` should have ``__exit__``.
  - An alembic migration with ``upgrade()`` should have ``downgrade()``.
  - A unittest class with ``setUp`` should have ``tearDown``.

These rules don't depend on what the rest of the codebase does.
A single class with ``__enter__`` and no ``__exit__`` is a gap
even if the rest of the project never uses context managers — the
*concept* of an enter implies an exit. That's the latin sense of
"lacuna" (a void structurally implied by everything around it),
which the frequency engine alone doesn't capture.

Output integrates with the existing Rule/Gap pipeline so the TUI,
suppress, and formatters all keep working unchanged. Symmetry
rules are emitted regardless of mining's ``min_confidence``
threshold — they're asserted, not statistical.
"""
from __future__ import annotations

from dataclasses import dataclass

from .entities import Entity
from .mining import Gap, Rule


@dataclass(frozen=True)
class SymmetryPair:
    """A configured (left, right) symmetry rule.

    ``scope`` is one of:
      - ``"class"`` — left and right must be methods of the same class
      - ``"file"`` — left and right must be functions in the same file
    """
    name: str
    left: str
    right: str
    scope: str
    description: str = ""


# Built-in pairs that apply to almost every Python codebase. Future
# work: user-configurable pairs via [[symmetry]] in lacuna.toml.
BUILTIN_PAIRS: list[SymmetryPair] = [
    SymmetryPair(
        "context_manager",
        "__enter__", "__exit__", "class",
        description="Context-manager protocol — every __enter__ pairs with __exit__",
    ),
    SymmetryPair(
        "async_context_manager",
        "__aenter__", "__aexit__", "class",
        description="Async context-manager protocol",
    ),
    SymmetryPair(
        "unittest_setup_teardown",
        "setUp", "tearDown", "class",
        description="unittest setup/teardown pairing",
    ),
    SymmetryPair(
        "alembic_migration",
        "upgrade", "downgrade", "file",
        description="Alembic migrations — every upgrade has a downgrade",
    ),
    SymmetryPair(
        "alembic_migration_short",
        "up", "down", "file",
        description="Short-form migrations — every up has a down",
    ),
]


def _short_name(entity: Entity) -> str:
    """Last component of a qualified_name. ``src/x.py::Foo.bar`` → ``bar``."""
    qn = entity.qualified_name
    after_double_colon = qn.rsplit("::", 1)[-1]
    return after_double_colon.rsplit(".", 1)[-1]


def _class_scope_key(entity: Entity) -> str | None:
    """Class scope: ``src/foo.py::MyClass`` for method ``src/foo.py::MyClass.bar``.

    Returns None if the entity isn't class-scoped (e.g. a free function).
    """
    if entity.kind != "method":
        return None
    qn = entity.qualified_name
    if "::" not in qn:
        return None
    file_part, after = qn.split("::", 1)
    if "." not in after:
        return None
    class_name = after.rsplit(".", 1)[0]
    return f"{file_part}::{class_name}"


def _file_scope_key(entity: Entity) -> str:
    """File scope: just the file path."""
    return entity.file_path


def find_symmetry_gaps(
    entities: dict[str, Entity],
    pairs: list[SymmetryPair] | None = None,
) -> tuple[list[Rule], list[Gap]]:
    """For each configured pair, find scopes that have ``left`` but not ``right``.

    Returns ``(rules, gaps)`` where each scope-level rule represents
    the configured pair (with global support_n/support_total
    indicating how widely the rule is honored across the corpus)
    and each gap is the offending ``left`` entity in a violating
    scope.
    """
    pairs = pairs if pairs is not None else BUILTIN_PAIRS

    by_class: dict[str, list[Entity]] = {}
    by_file: dict[str, list[Entity]] = {}

    for ent in entities.values():
        if ent.kind == "method":
            class_key = _class_scope_key(ent)
            if class_key is not None:
                by_class.setdefault(class_key, []).append(ent)
        if ent.kind in ("function", "method"):
            by_file.setdefault(_file_scope_key(ent), []).append(ent)

    rules: list[Rule] = []
    gaps: list[Gap] = []

    for pair in pairs:
        scope_index = by_class if pair.scope == "class" else by_file

        # For each scope, find members whose short_name matches left/right.
        scopes_with_left: dict[str, Entity] = {}
        scopes_with_right: set[str] = set()

        for scope_id, members in scope_index.items():
            for ent in members:
                name = _short_name(ent)
                if name == pair.left and scope_id not in scopes_with_left:
                    scopes_with_left[scope_id] = ent
                elif name == pair.right:
                    scopes_with_right.add(scope_id)

        if not scopes_with_left:
            continue  # the pair doesn't apply to this corpus at all

        violators = [
            (scope_id, ent) for scope_id, ent in scopes_with_left.items()
            if scope_id not in scopes_with_right
        ]

        # One rule per pair, with corpus-wide support showing how
        # widely the pair is honored. Confidence is computed from
        # those numbers automatically by Rule.confidence.
        n_total = len(scopes_with_left)
        n_with_both = n_total - len(violators)
        rule = Rule(
            group_id=f"symmetry:{pair.name}",
            feature_kind="symmetry",
            feature_value=pair.right,
            support_n=n_with_both,
            support_total=n_total,
        )
        rules.append(rule)

        for _scope_id, left_ent in violators:
            gaps.append(Gap(rule_id=rule.id, entity_id=left_ent.id))

    return rules, gaps

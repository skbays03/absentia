"""Symmetry pair detection — the second mining strategy.

Frequency mining (the original engine) catches "most do X; this
doesn't." Symmetry catches a different shape of gap: **entities
that have a "left" without a corresponding "right"**, where the
relationship is structural.

There are two sources of pairs:

1. **Language-protocol pairs** (hardcoded). These are language
   contracts the runtime requires, not project conventions. Every
   Python codebase that uses one needs the other:

      ``__enter__``  ↔  ``__exit__``     (the ``with`` statement)
      ``__aenter__`` ↔  ``__aexit__``    (``async with``)

2. **Mined pairs** (per-corpus). These come from the codebase's
   own naming conventions. The miner finds method/function names
   that co-occur in ≥80% of scopes containing either one, with at
   least one violator. Examples that *typically* mine themselves:

      ``setUp``      ↔  ``tearDown``    (unittest convention)
      ``upgrade``    ↔  ``downgrade``   (alembic migrations)
      ``register``   ↔  ``unregister``  (project event-bus, etc.)

This split keeps the philosophy honest: the rules come from your
code itself (mining), with a tiny fallback for things the language
itself enforces (hardcoded). Other "common" conventions like
unittest setUp/tearDown will be auto-mined when a project actually
uses them — and won't fire spuriously when a project uses pytest's
fixture model instead.

Output integrates with the existing Rule/Gap pipeline so the TUI,
suppress, and formatters all keep working unchanged. Symmetry
rules are emitted regardless of mining's ``min_confidence``
threshold — they're asserted, not statistical (the threshold lives
inside the miner instead).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .entities import Entity, FeatureSet
from .mining import Gap, Rule


@dataclass(frozen=True)
class SymmetryPair:
    """A (left, right) symmetry rule.

    ``scope`` is one of:
      - ``"class"`` — left and right must be methods of the same class
      - ``"file"`` — left and right must be functions in the same file
    """
    name: str
    left: str
    right: str
    scope: str
    description: str = ""


# Hardcoded pairs: language contracts the runtime enforces. Project
# conventions are auto-mined from the corpus instead — see
# :func:`mine_symmetry_pairs`.
BUILTIN_PAIRS: list[SymmetryPair] = [
    SymmetryPair(
        "context_manager",
        "__enter__", "__exit__", "class",
        description="Context-manager protocol — Python's `with` requires both",
    ),
    SymmetryPair(
        "async_context_manager",
        "__aenter__", "__aexit__", "class",
        description="Async context-manager protocol — `async with` requires both",
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


def mine_symmetry_pairs(
    entities: dict[str, Entity],
    *,
    min_support: int = 3,
    min_confidence: float = 0.8,
) -> list[SymmetryPair]:
    """Auto-discover symmetry pairs from corpus naming patterns.

    For every pair of method/function short-names that co-occur in
    the same scope (class for methods, file for functions), compute
    the directional confidence:

        confidence(left → right) = scopes_with_both / scopes_with_left

    Emit a SymmetryPair when:

    - ``scopes_with_left ≥ min_support`` (default 3 — too few names is noise)
    - ``confidence ≥ min_confidence`` (default 0.8 — strong asymmetry)
    - At least one violator exists (otherwise no gap to flag)

    The output is fed into :func:`find_symmetry_gaps` alongside the
    hardcoded language-protocol pairs.
    """
    by_class: dict[str, set[str]] = {}
    by_file: dict[str, set[str]] = {}

    for ent in entities.values():
        if ent.kind not in ("function", "method"):
            continue
        name = _short_name(ent)
        if ent.kind == "method":
            class_key = _class_scope_key(ent)
            if class_key is not None:
                by_class.setdefault(class_key, set()).add(name)
        if ent.kind in ("function", "method"):
            by_file.setdefault(_file_scope_key(ent), set()).add(name)

    pairs: list[SymmetryPair] = []
    pairs.extend(_mine_pairs_for_scope(by_class, "class", min_support, min_confidence))
    pairs.extend(_mine_pairs_for_scope(by_file, "file", min_support, min_confidence))
    return pairs


def _mine_pairs_for_scope(
    scope_index: dict[str, set[str]],
    scope: str,
    min_support: int,
    min_confidence: float,
) -> list[SymmetryPair]:
    """Mine pairs for a single scope kind."""
    name_count: Counter[str] = Counter()
    for names in scope_index.values():
        for n in names:
            name_count[n] += 1

    # Pair counts. Iterate sorted names to dedupe (n1, n2) vs (n2, n1).
    pair_count: Counter[tuple[str, str]] = Counter()
    for names in scope_index.values():
        sorted_names = sorted(names)
        for i, n1 in enumerate(sorted_names):
            for n2 in sorted_names[i + 1:]:
                pair_count[(n1, n2)] += 1

    pairs: list[SymmetryPair] = []
    seen: set[tuple[str, str, str]] = set()  # (left, right, scope)

    for (n1, n2), both in pair_count.items():
        for left, right in ((n1, n2), (n2, n1)):
            count_left = name_count[left]
            if count_left < min_support:
                continue
            conf = both / count_left
            if conf < min_confidence:
                continue
            # Skip when there's no violator — pure observation, not a rule.
            if both >= count_left:
                continue
            key = (left, right, scope)
            if key in seen:
                continue
            seen.add(key)
            pairs.append(SymmetryPair(
                name=f"mined:{left}_to_{right}",
                left=left,
                right=right,
                scope=scope,
                description=(
                    f"Mined: {both}/{count_left} {scope}s with "
                    f"`{left}` also have `{right}`"
                ),
            ))
    return pairs


def find_call_pair_gaps(
    entities: dict[str, Entity],
    feature_index: dict[str, FeatureSet],
    *,
    min_support: int = 5,
    min_confidence: float = 0.9,
) -> tuple[list[Rule], list[Gap]]:
    """Mine paired-call symmetries within function scope.

    For each pair of names called by the same function, compute the
    directional confidence::

        conf(left → right) = functions calling both / functions calling left

    When ``conf ≥ min_confidence`` (default 0.9 — strict, because call
    sets are noisy), the pair is treated as a project convention. Each
    function that calls ``left`` but not ``right`` is a gap.

    Examples this catches: ``bus.subscribe`` ↔ ``bus.unsubscribe``,
    ``audit.begin`` ↔ ``audit.commit``, ``trace.start`` ↔ ``trace.stop``,
    ``acquire`` ↔ ``release`` — project-specific resource pairs that
    no off-the-shelf linter knows about.

    Doesn't try control-flow analysis. The check is "this function
    calls A but not B"; if you call B in the wrong place, lacuna
    won't catch that — that's linter / type-system territory.

    The default ``min_confidence=0.9`` and ``min_support=5`` are
    conservative on purpose: call sets contain language built-ins
    (``len``, ``print``, ``str``) that co-occur with nearly everything;
    a strict threshold filters most spurious pairs.
    """
    # Index: function_id → set of call names
    call_sets: dict[str, frozenset[str]] = {}
    for entity_id, fs in feature_index.items():
        if entity_id not in entities:
            continue
        if entities[entity_id].kind not in ("function", "method"):
            continue
        calls = fs.get_set("calls")
        if calls:
            call_sets[entity_id] = calls

    if not call_sets:
        return [], []

    # Pass 1: name frequency. Filter to "popular enough to mine."
    name_count: Counter[str] = Counter()
    callers_by_name: dict[str, set[str]] = {}
    for caller_id, calls in call_sets.items():
        for c in calls:
            name_count[c] += 1
            callers_by_name.setdefault(c, set()).add(caller_id)
    popular = {n for n, c in name_count.items() if c >= min_support}
    if not popular:
        return [], []

    # Pass 2: pair counts among popular names only
    pair_count: Counter[tuple[str, str]] = Counter()
    for calls in call_sets.values():
        relevant = sorted(c for c in calls if c in popular)
        for i, n1 in enumerate(relevant):
            for n2 in relevant[i + 1:]:
                pair_count[(n1, n2)] += 1

    # Pass 3: emit rules + gaps. Use the precomputed callers_by_name
    # index so violators = callers_of_left - callers_of_right is an
    # O(1) set difference instead of a per-pair O(N) scan over every
    # function. On the Linux kernel that's the difference between
    # tens of seconds and milliseconds.
    rules: list[Rule] = []
    gaps: list[Gap] = []
    seen: set[tuple[str, str]] = set()
    empty_set: set[str] = set()

    for (n1, n2), both in pair_count.items():
        for left, right in ((n1, n2), (n2, n1)):
            count_left = name_count[left]
            if count_left < min_support:
                continue
            conf = both / count_left
            if conf < min_confidence:
                continue
            # Need at least one violator
            if both >= count_left:
                continue
            if (left, right) in seen:
                continue
            seen.add((left, right))

            rule = Rule(
                group_id=f"call_pair:{left}",
                feature_kind="call_pair",
                feature_value=right,
                support_n=both,
                support_total=count_left,
            )
            rules.append(rule)

            # O(1) violator extraction: callers that have `left` but
            # don't have `right`.
            violators = callers_by_name[left] - callers_by_name.get(
                right, empty_set,
            )
            rule_id = rule.id
            gaps.extend(Gap(rule_id=rule_id, entity_id=cid) for cid in violators)

    return rules, gaps


def find_symmetry_gaps(
    entities: dict[str, Entity],
    pairs: list[SymmetryPair] | None = None,
    *,
    auto_mine: bool = True,
) -> tuple[list[Rule], list[Gap]]:
    """For each configured pair, find scopes that have ``left`` but not ``right``.

    When ``pairs`` is None (the default), the engine combines:

    - The hardcoded ``BUILTIN_PAIRS`` (language protocols only)
    - Auto-mined pairs from the corpus (project conventions), iff
      ``auto_mine`` is True.

    Pass an explicit ``pairs`` list to override both — useful for
    tests or for users with a fully custom pair configuration.
    """
    if pairs is None:
        pairs = list(BUILTIN_PAIRS)
        if auto_mine:
            pairs.extend(mine_symmetry_pairs(entities))

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

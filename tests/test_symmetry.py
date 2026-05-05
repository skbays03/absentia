"""Tests for src/lacuna/symmetry.py."""
from __future__ import annotations

from lacuna.entities import Entity
from lacuna.symmetry import (
    BUILTIN_PAIRS,
    SymmetryPair,
    find_symmetry_gaps,
)


def _method(file_path: str, class_name: str, name: str) -> Entity:
    return Entity(
        kind="method",
        qualified_name=f"{file_path}::{class_name}.{name}",
        file_path=file_path,
        line=1,
    )


def _func(file_path: str, name: str) -> Entity:
    return Entity(
        kind="function",
        qualified_name=f"{file_path}::{name}",
        file_path=file_path,
        line=1,
    )


# ── Class-scoped pairs ────────────────────────────────────────────────


def test_context_manager_complete_no_gap():
    """A class with both __enter__ and __exit__ produces no gaps."""
    enter_m = _method("src/foo.py", "Ctx", "__enter__")
    exit_m = _method("src/foo.py", "Ctx", "__exit__")
    entities = {enter_m.id: enter_m, exit_m.id: exit_m}

    rules, gaps = find_symmetry_gaps(entities)

    # Rule may be emitted (1 of 1 honor it), but no gaps.
    cm_rules = [r for r in rules if "context_manager" in r.group_id]
    assert len(gaps) == 0
    assert all(g.entity_id != enter_m.id for g in gaps)
    if cm_rules:
        assert cm_rules[0].support_n == cm_rules[0].support_total


def test_context_manager_missing_exit_is_gap():
    """A class with __enter__ but no __exit__ surfaces a gap."""
    enter_m = _method("src/foo.py", "Ctx", "__enter__")
    entities = {enter_m.id: enter_m}

    rules, gaps = find_symmetry_gaps(entities)

    cm_rules = [r for r in rules if "context_manager" in r.group_id]
    assert len(cm_rules) == 1
    assert cm_rules[0].feature_value == "__exit__"
    assert len(gaps) == 1
    assert gaps[0].entity_id == enter_m.id


def test_context_manager_partial_corpus():
    """When 1 of 2 classes with __enter__ also has __exit__, gap is the other."""
    a_enter = _method("src/a.py", "A", "__enter__")
    a_exit = _method("src/a.py", "A", "__exit__")
    b_enter = _method("src/b.py", "B", "__enter__")
    entities = {e.id: e for e in (a_enter, a_exit, b_enter)}

    rules, gaps = find_symmetry_gaps(entities)

    cm_rules = [r for r in rules if "context_manager" in r.group_id]
    assert len(cm_rules) == 1
    rule = cm_rules[0]
    assert rule.support_n == 1
    assert rule.support_total == 2
    assert abs(rule.confidence - 0.5) < 0.01
    # Single gap: B
    cm_gaps = [g for g in gaps if g.rule_id == rule.id]
    assert len(cm_gaps) == 1
    assert cm_gaps[0].entity_id == b_enter.id


def test_unittest_setup_teardown_pair():
    """setUp without tearDown surfaces a gap."""
    setup = _method("tests/test_x.py", "TestX", "setUp")
    entities = {setup.id: setup}

    rules, gaps = find_symmetry_gaps(entities)

    rule = next((r for r in rules if "unittest_setup_teardown" in r.group_id), None)
    assert rule is not None
    assert rule.feature_value == "tearDown"
    assert any(g.entity_id == setup.id for g in gaps)


def test_async_context_manager_pair():
    """__aenter__ without __aexit__ is a gap."""
    enter = _method("src/x.py", "A", "__aenter__")
    entities = {enter.id: enter}

    rules, gaps = find_symmetry_gaps(entities)

    rule = next((r for r in rules if "async_context_manager" in r.group_id), None)
    assert rule is not None
    assert rule.feature_value == "__aexit__"
    assert any(g.entity_id == enter.id for g in gaps)


# ── File-scoped pairs ─────────────────────────────────────────────────


def test_alembic_migration_complete_no_gap():
    """File with both upgrade and downgrade produces no gap."""
    up = _func("migrations/0001_users.py", "upgrade")
    down = _func("migrations/0001_users.py", "downgrade")
    entities = {up.id: up, down.id: down}

    rules, gaps = find_symmetry_gaps(entities)
    assert len(gaps) == 0


def test_alembic_migration_missing_downgrade():
    """File with only upgrade is a gap."""
    up = _func("migrations/0001_users.py", "upgrade")
    entities = {up.id: up}

    rules, gaps = find_symmetry_gaps(entities)

    rule = next((r for r in rules if "alembic_migration" == r.group_id.split(":")[-1]), None)
    assert rule is not None
    assert rule.feature_value == "downgrade"
    assert any(g.entity_id == up.id for g in gaps)


def test_short_form_migration_pair():
    """up()/down() pair (short-form migrations)."""
    up = _func("migrations/0001_x.py", "up")
    entities = {up.id: up}

    rules, gaps = find_symmetry_gaps(entities)

    rule = next((r for r in rules if "alembic_migration_short" in r.group_id), None)
    assert rule is not None
    assert rule.feature_value == "down"


# ── Edge cases ────────────────────────────────────────────────────────


def test_no_gap_when_pair_doesnt_apply():
    """A corpus with no enter/exit/setUp/upgrade/etc. produces no symmetry rules."""
    f = _func("src/foo.py", "regular_function")
    entities = {f.id: f}

    rules, gaps = find_symmetry_gaps(entities)
    assert rules == []
    assert gaps == []


def test_method_in_different_classes_doesnt_cross():
    """__enter__ in class A and __exit__ in class B don't pair across classes."""
    a_enter = _method("src/x.py", "A", "__enter__")
    b_exit = _method("src/x.py", "B", "__exit__")
    entities = {a_enter.id: a_enter, b_exit.id: b_exit}

    rules, gaps = find_symmetry_gaps(entities)

    # A is a gap (has __enter__, no __exit__)
    assert any(g.entity_id == a_enter.id for g in gaps)


def test_custom_pair():
    """Caller-supplied pairs override the builtins."""
    ent = _method("src/x.py", "Resource", "acquire")
    entities = {ent.id: ent}

    custom = [
        SymmetryPair("acquire_release", "acquire", "release", "class"),
    ]
    rules, gaps = find_symmetry_gaps(entities, pairs=custom)

    assert len(rules) == 1
    assert rules[0].feature_value == "release"
    assert any(g.entity_id == ent.id for g in gaps)


def test_rule_id_is_stable():
    """Same input → same rule.id (suppression depends on this)."""
    enter = _method("src/x.py", "Ctx", "__enter__")
    entities = {enter.id: enter}

    rules1, _ = find_symmetry_gaps(entities)
    rules2, _ = find_symmetry_gaps(entities)
    assert [r.id for r in rules1] == [r.id for r in rules2]


def test_builtin_pairs_have_descriptions():
    """Built-in pairs should be self-documenting for the user-facing report."""
    for pair in BUILTIN_PAIRS:
        assert pair.description, f"pair {pair.name} has no description"

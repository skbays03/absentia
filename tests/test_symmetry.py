"""Tests for src/absentia/symmetry.py."""
from __future__ import annotations

from absentia.entities import Entity, FeatureSet
from absentia.symmetry import (
    BUILTIN_PAIRS,
    SymmetryPair,
    find_call_pair_gaps,
    find_symmetry_gaps,
    mine_symmetry_pairs,
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


def test_async_context_manager_pair():
    """__aenter__ without __aexit__ is a gap."""
    enter = _method("src/x.py", "A", "__aenter__")
    entities = {enter.id: enter}

    rules, gaps = find_symmetry_gaps(entities)

    rule = next((r for r in rules if "async_context_manager" in r.group_id), None)
    assert rule is not None
    assert rule.feature_value == "__aexit__"
    assert any(g.entity_id == enter.id for g in gaps)


# ── Auto-mined convention pairs ──────────────────────────────────────


def test_mine_class_scoped_pair_setup_teardown():
    """When most classes have both setUp and tearDown, mine the pair and
    flag the one without tearDown."""
    entities = {}
    # 4 classes have both
    for i in range(4):
        s = _method(f"tests/t_{i}.py", f"T{i}", "setUp")
        td = _method(f"tests/t_{i}.py", f"T{i}", "tearDown")
        entities[s.id] = s
        entities[td.id] = td
    # 1 class has only setUp (the violator)
    only_setup = _method("tests/t_4.py", "T4", "setUp")
    entities[only_setup.id] = only_setup

    pairs = mine_symmetry_pairs(entities, min_support=3, min_confidence=0.8)
    setup_to_teardown = [
        p for p in pairs
        if p.left == "setUp" and p.right == "tearDown" and p.scope == "class"
    ]
    assert len(setup_to_teardown) == 1
    assert setup_to_teardown[0].name.startswith("mined:")

    # End-to-end: find_symmetry_gaps with auto_mine=True flags the violator.
    rules, gaps = find_symmetry_gaps(entities, auto_mine=True)
    assert any(g.entity_id == only_setup.id for g in gaps)


def test_mine_file_scoped_pair_upgrade_downgrade():
    """Alembic-style: file with upgrade should also have downgrade.
    Mined when most files honor it."""
    entities = {}
    # 5 migrations have both
    for i in range(5):
        u = _func(f"migrations/{i:04d}_x.py", "upgrade")
        d = _func(f"migrations/{i:04d}_x.py", "downgrade")
        entities[u.id] = u
        entities[d.id] = d
    # 1 has only upgrade
    broken = _func("migrations/0099_broken.py", "upgrade")
    entities[broken.id] = broken

    pairs = mine_symmetry_pairs(entities)
    up_to_down = [
        p for p in pairs
        if p.left == "upgrade" and p.right == "downgrade" and p.scope == "file"
    ]
    assert len(up_to_down) == 1


def test_mine_skips_pairs_below_min_support():
    """Pairs in fewer than min_support scopes are dropped (noise)."""
    entities = {}
    # Only 2 classes with the pair — below default min_support=3
    for i in range(2):
        a = _method("src/x.py", f"C{i}", "begin")
        b = _method("src/x.py", f"C{i}", "end")
        entities[a.id] = a
        entities[b.id] = b
    only_begin = _method("src/x.py", "C9", "begin")
    entities[only_begin.id] = only_begin

    pairs = mine_symmetry_pairs(entities, min_support=3)
    assert pairs == []


def test_mine_skips_pairs_with_no_violator():
    """If every scope honors the pair, the engine doesn't bother
    emitting it — there's nothing to flag."""
    entities = {}
    # 5 classes, all with both
    for i in range(5):
        a = _method(f"src/x_{i}.py", f"C{i}", "alpha")
        b = _method(f"src/x_{i}.py", f"C{i}", "beta")
        entities[a.id] = a
        entities[b.id] = b

    pairs = mine_symmetry_pairs(entities)
    assert all(
        p.left != "alpha" and p.right != "beta" for p in pairs
    )


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


# ── Call-pair frequency mining (function scope) ──────────────────────


def _function_with_calls(file_path: str, name: str, calls: list[str]) -> tuple[Entity, FeatureSet]:
    ent = _func(file_path, name)
    fs = FeatureSet(by_kind={"calls": frozenset(calls)})
    return ent, fs


def test_call_pair_subscribe_unsubscribe():
    """Functions calling subscribe should also call unsubscribe.
    A function that calls subscribe but not unsubscribe is a gap.
    """
    entities = {}
    feature_index = {}
    # 10 paired calls — comfortably above default min_confidence=0.9
    for i in range(10):
        ent, fs = _function_with_calls(
            f"src/handler_{i}.py", f"handle_{i}",
            ["bus.subscribe", "bus.unsubscribe", "logger.info"],
        )
        entities[ent.id] = ent
        feature_index[ent.id] = fs
    # 1 violator
    bad, bad_fs = _function_with_calls(
        "src/leaky.py", "leaky_handler",
        ["bus.subscribe", "logger.info"],  # missing unsubscribe
    )
    entities[bad.id] = bad
    feature_index[bad.id] = bad_fs

    rules, gaps = find_call_pair_gaps(entities, feature_index)

    # Rule with left=bus.subscribe, right=bus.unsubscribe
    matching = [
        r for r in rules
        if r.feature_value == "bus.unsubscribe"
        and "bus.subscribe" in r.group_id
    ]
    assert len(matching) == 1
    rule = matching[0]
    # 10 functions call both, 11 call subscribe → confidence 10/11 ≈ 0.91
    assert rule.support_n == 10
    assert rule.support_total == 11

    # The leaky handler is a gap
    leaky_gaps = [g for g in gaps if g.entity_id == bad.id and g.rule_id == rule.id]
    assert len(leaky_gaps) == 1


def test_call_pair_no_violators_no_rule():
    """If every caller of left also calls right, there's no asymmetry."""
    entities = {}
    feature_index = {}
    for i in range(5):
        ent, fs = _function_with_calls(
            f"src/h_{i}.py", f"handle_{i}",
            ["lock", "release"],
        )
        entities[ent.id] = ent
        feature_index[ent.id] = fs

    rules, gaps = find_call_pair_gaps(entities, feature_index)
    # No rules emitted because no violator
    assert all(r.feature_value != "release" for r in rules)
    assert all(r.feature_value != "lock" for r in rules)


def test_call_pair_below_min_support():
    """Pairs called by fewer than min_support functions don't fire."""
    entities = {}
    feature_index = {}
    # Only 2 functions call begin/commit
    for i in range(2):
        ent, fs = _function_with_calls(
            f"src/x_{i}.py", f"f_{i}",
            ["audit.begin", "audit.commit"],
        )
        entities[ent.id] = ent
        feature_index[ent.id] = fs
    bad, bad_fs = _function_with_calls(
        "src/bad.py", "bad",
        ["audit.begin"],
    )
    entities[bad.id] = bad
    feature_index[bad.id] = bad_fs

    rules, gaps = find_call_pair_gaps(
        entities, feature_index, min_support=5,
    )
    # 3 callers of audit.begin → below min_support=5
    assert all(r.feature_value != "audit.commit" for r in rules)


def test_call_pair_strict_confidence_filters_noise():
    """At default min_confidence=0.9, weak co-occurrences are filtered."""
    entities = {}
    feature_index = {}
    # 5 callers of A: 3 also call B (60%), 2 don't
    for i in range(3):
        ent, fs = _function_with_calls(f"src/y_{i}.py", f"yes_{i}", ["A", "B"])
        entities[ent.id] = ent
        feature_index[ent.id] = fs
    for i in range(2):
        ent, fs = _function_with_calls(f"src/n_{i}.py", f"no_{i}", ["A"])
        entities[ent.id] = ent
        feature_index[ent.id] = fs

    rules, gaps = find_call_pair_gaps(
        entities, feature_index, min_confidence=0.9,
    )
    # 60% confidence is well below 0.9 — no rule
    assert all(r.feature_value != "B" or "A" not in r.group_id for r in rules)


def test_call_pair_handles_empty_corpus():
    """No functions, no crash."""
    rules, gaps = find_call_pair_gaps({}, {})
    assert rules == []
    assert gaps == []

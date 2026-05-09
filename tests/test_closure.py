"""Tests for src/absentia/closure.py — unused-class detection (Item H)."""
from __future__ import annotations

from absentia.closure import find_unused_class_gaps
from absentia.entities import Entity, FeatureSet


def _class(file_path: str, name: str) -> Entity:
    return Entity(
        kind="class",
        qualified_name=f"{file_path}::{name}",
        file_path=file_path,
        line=1,
    )


def _function(file_path: str, name: str) -> Entity:
    return Entity(
        kind="function",
        qualified_name=f"{file_path}::{name}",
        file_path=file_path,
        line=1,
    )


def _features(**kinds: set[str]) -> FeatureSet:
    return FeatureSet(by_kind={k: frozenset(v) for k, v in kinds.items()})


def test_class_with_zero_references_is_flagged():
    """Classic case: a class is defined and nothing else points at it."""
    cls = _class("src/dead.py", "Forgotten")
    fn = _function("src/main.py", "main")
    entities = {cls.id: cls, fn.id: fn}
    feature_index = {
        cls.id: FeatureSet(),
        fn.id: _features(calls={"some_other_thing"}),
    }
    rules, gaps = find_unused_class_gaps(entities, feature_index)
    assert len(rules) == 1
    assert "Forgotten" in rules[0].feature_value
    assert len(gaps) == 1
    assert gaps[0].entity_id == cls.id


def test_class_referenced_via_calls_is_not_flagged():
    """A class instantiated somewhere shows up as a call; spare it."""
    cls = _class("src/user.py", "User")
    fn = _function("src/main.py", "make_user")
    entities = {cls.id: cls, fn.id: fn}
    feature_index = {
        cls.id: FeatureSet(),
        fn.id: _features(calls={"User"}),
    }
    rules, _ = find_unused_class_gaps(entities, feature_index)
    assert rules == []


def test_class_referenced_via_inheritance_is_not_flagged():
    """A base class with subclasses is being used. The subclass
    itself may also be unused — that's a separate gap, not what
    this test is checking. Assert specifically that the BASE
    isn't in the flagged set."""
    base = _class("src/base.py", "Vehicle")
    car = _class("src/car.py", "Car")
    user = _function("src/main.py", "main")
    entities = {base.id: base, car.id: car, user.id: user}
    feature_index = {
        base.id: FeatureSet(),
        car.id: _features(parent_class={"Vehicle"}),
        # Spare Car too so we test the inheritance link cleanly.
        user.id: _features(calls={"Car"}),
    }
    _, gaps = find_unused_class_gaps(entities, feature_index)
    flagged_ids = {g.entity_id for g in gaps}
    assert base.id not in flagged_ids
    assert car.id not in flagged_ids


def test_class_referenced_via_decorator_is_not_flagged():
    """``@route`` decorating a function references the route class."""
    cls = _class("src/router.py", "route")
    fn = _function("src/handlers.py", "list_users")
    entities = {cls.id: cls, fn.id: fn}
    feature_index = {
        cls.id: FeatureSet(),
        fn.id: _features(decorator={"@route"}),
    }
    rules, _ = find_unused_class_gaps(entities, feature_index)
    assert rules == []


def test_dotted_reference_matches_bare_name():
    """``calls={"pkg.User"}`` should still count as a reference to
    a class named ``User`` — _normalize_reference yields both the
    dotted form and the bare last segment."""
    cls = _class("src/user.py", "User")
    fn = _function("src/main.py", "go")
    entities = {cls.id: cls, fn.id: fn}
    feature_index = {
        cls.id: FeatureSet(),
        fn.id: _features(calls={"models.User"}),
    }
    rules, _ = find_unused_class_gaps(entities, feature_index)
    assert rules == []


def test_decorator_at_prefix_stripped():
    """``decorator={"@Foo"}`` references class Foo, not "@Foo"."""
    cls = _class("src/d.py", "Foo")
    fn = _function("src/h.py", "handler")
    entities = {cls.id: cls, fn.id: fn}
    feature_index = {
        cls.id: FeatureSet(),
        fn.id: _features(decorator={"@Foo"}),
    }
    rules, _ = find_unused_class_gaps(entities, feature_index)
    assert rules == []


def test_private_class_is_not_flagged():
    """Leading-underscore classes are by-convention private; absentia
    doesn't claim to know what's used internally."""
    cls = _class("src/util.py", "_PrivateHelper")
    fn = _function("src/main.py", "main")
    entities = {cls.id: cls, fn.id: fn}
    feature_index = {
        cls.id: FeatureSet(),
        fn.id: _features(calls={"something_else"}),
    }
    rules, _ = find_unused_class_gaps(entities, feature_index)
    assert rules == []


def test_method_calling_back_to_its_own_class_counts_as_use():
    """If a class's method calls the class itself (e.g. factory-style
    ``X.create()`` or recursive instantiation), that's a real use of
    the class. The simplified closure index doesn't track entity-id
    provenance, so any reference anywhere counts — which is the
    right call: a class with internal recursion is genuinely in use."""
    cls = _class("src/x.py", "X")
    method_id = "src/x.py::X.factory"
    entities = {cls.id: cls}
    feature_index = {
        cls.id: FeatureSet(),
        # Method's calls set mentioning the class name.
        method_id: _features(calls={"X"}),
    }
    rules, _ = find_unused_class_gaps(entities, feature_index)
    assert rules == []


def test_function_entities_are_not_flagged():
    """v1 only flags classes; functions have noisier signal (CLI
    entry points, framework hooks, test functions, plugins...)."""
    fn = _function("src/x.py", "isolated_function")
    other = _function("src/y.py", "something")
    entities = {fn.id: fn, other.id: other}
    feature_index = {
        fn.id: FeatureSet(),
        other.id: _features(calls={"foo"}),
    }
    rules, _ = find_unused_class_gaps(entities, feature_index)
    assert rules == []


def test_empty_corpus_no_crash():
    rules, gaps = find_unused_class_gaps({}, {})
    assert rules == []
    assert gaps == []

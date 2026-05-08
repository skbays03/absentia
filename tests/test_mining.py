from absentia.entities import FeatureSet
from absentia.mining import mine
from absentia.selectors import Group


def _features(*decorators: str) -> FeatureSet:
    return FeatureSet(by_kind={"decorator": frozenset(decorators)})


def test_mines_single_rule_when_decorator_meets_threshold():
    members = ["create", "update", "list", "get", "delete"]
    group = Group(name="api", selector_type="directory", members=tuple(members))
    feature_index = {
        "create": _features("@audit"),
        "update": _features("@audit"),
        "list":   _features("@audit"),
        "get":    _features("@audit"),
        "delete": _features(),  # the gap
    }
    rules, gaps = mine([group], feature_index, min_confidence=0.8)
    assert len(rules) == 1
    rule = rules[0]
    assert rule.feature_value == "@audit"
    assert rule.support_n == 4
    assert rule.support_total == 5
    assert rule.confidence == 0.8
    assert len(gaps) == 1
    assert gaps[0].entity_id == "delete"


def test_no_rule_below_threshold():
    members = ["a", "b", "c", "d", "e"]
    group = Group(name="g", selector_type="directory", members=tuple(members))
    feature_index = {
        "a": _features("@x"),
        "b": _features("@x"),
        "c": _features("@x"),
        "d": _features(),
        "e": _features(),
    }
    rules, gaps = mine([group], feature_index, min_confidence=0.8)
    assert rules == []
    assert gaps == []


def test_unanimous_rule_emits_zero_gaps():
    members = ["a", "b", "c"]
    group = Group(name="g", selector_type="directory", members=tuple(members))
    feature_index = {n: _features("@always") for n in members}
    rules, gaps = mine([group], feature_index, min_confidence=0.8)
    assert len(rules) == 1
    assert rules[0].confidence == 1.0
    assert gaps == []


def test_multiple_rules_can_emerge_from_one_group():
    members = ["a", "b", "c", "d", "e"]
    group = Group(name="g", selector_type="directory", members=tuple(members))
    feature_index = {
        "a": _features("@x", "@y"),
        "b": _features("@x", "@y"),
        "c": _features("@x", "@y"),
        "d": _features("@x", "@y"),
        "e": _features("@x"),  # missing @y
    }
    rules, gaps = mine([group], feature_index, min_confidence=0.8)
    by_value = {r.feature_value: r for r in rules}
    assert set(by_value) == {"@x", "@y"}
    assert by_value["@x"].confidence == 1.0
    assert by_value["@y"].confidence == 0.8
    # Only @y produces a gap; @x is unanimous
    assert {g.entity_id for g in gaps} == {"e"}


def test_empty_group_yields_no_rules():
    group = Group(name="empty", selector_type="directory", members=())
    rules, gaps = mine([group], {}, min_confidence=0.8)
    assert rules == []
    assert gaps == []


def test_decorator_group_skips_trivial_self_rule():
    """A decorator group of @audit fns trivially has @audit at 100%.
    That's noise; mining should skip it."""
    members = ["a", "b", "c"]
    group = Group(name="@audit", selector_type="decorator", members=tuple(members))
    feature_index = {n: _features("@audit") for n in members}
    rules, gaps = mine([group], feature_index, min_confidence=0.8,
                       feature_kind="decorator")
    assert rules == []  # trivial self-rule filtered
    assert gaps == []


def test_mining_skips_members_without_the_feature_kind():
    """A directory group mixing classes (with parent_class) and functions
    (without) should mine parent_class only across the classes — and not
    flag the functions as 'missing' something they couldn't have."""
    members = ["c1", "c2", "c3", "f1", "f2"]
    group = Group(name="mixed", selector_type="directory", members=tuple(members))
    feature_index = {
        "c1": FeatureSet(by_kind={"parent_class": frozenset({"Base"})}),
        "c2": FeatureSet(by_kind={"parent_class": frozenset({"Base"})}),
        "c3": FeatureSet(by_kind={"parent_class": frozenset({"Base"})}),
        # Functions don't have parent_class in their FeatureSet at all.
        "f1": FeatureSet(by_kind={"calls": frozenset({"helper"})}),
        "f2": FeatureSet(by_kind={"calls": frozenset({"helper"})}),
    }
    rules, gaps = mine([group], feature_index, min_confidence=0.8,
                       feature_kind="parent_class")
    assert len(rules) == 1
    assert rules[0].support_n == 3
    assert rules[0].support_total == 3  # functions excluded from denominator
    assert gaps == []  # no eligible member is missing the value


def test_self_reference_parent_class_gap_filtered():
    """If a class is the dominant parent_class within a directory group,
    the base class itself can't be flagged as 'missing' itself."""
    members = ["a.py::HttpException", "a.py::NotFound", "a.py::ServerError",
               "a.py::Unauthorized", "a.py::BadRequest"]
    group = Group(name="a", selector_type="directory", members=tuple(members))
    feature_index = {
        # Subclasses extend HttpException
        "a.py::NotFound":     FeatureSet(by_kind={"parent_class": frozenset({"HttpException"})}),
        "a.py::ServerError":  FeatureSet(by_kind={"parent_class": frozenset({"HttpException"})}),
        "a.py::Unauthorized": FeatureSet(by_kind={"parent_class": frozenset({"HttpException"})}),
        "a.py::BadRequest":   FeatureSet(by_kind={"parent_class": frozenset({"HttpException"})}),
        # Base class itself extends Exception (or similar)
        "a.py::HttpException": FeatureSet(by_kind={"parent_class": frozenset({"Exception"})}),
    }
    rules, gaps = mine([group], feature_index, min_confidence=0.8,
                       feature_kind="parent_class")
    # A rule for HttpException (4/5 = 0.8) should fire; but the base class
    # itself shouldn't be flagged as a self-reference gap.
    assert any(r.feature_value == "HttpException" for r in rules)
    flagged = {g.entity_id for g in gaps}
    assert "a.py::HttpException" not in flagged, (
        "the base class should never be flagged as missing itself"
    )


def test_decorator_group_finds_co_occurring_decorator():
    """In an @audit group, if 4/5 also have @route, that's a useful rule."""
    members = ["a", "b", "c", "d", "e"]
    group = Group(name="@audit", selector_type="decorator", members=tuple(members))
    feature_index = {
        "a": _features("@audit", "@route"),
        "b": _features("@audit", "@route"),
        "c": _features("@audit", "@route"),
        "d": _features("@audit", "@route"),
        "e": _features("@audit"),  # missing @route
    }
    rules, gaps = mine([group], feature_index, min_confidence=0.8,
                       feature_kind="decorator")
    assert len(rules) == 1
    assert rules[0].feature_value == "@route"
    assert {g.entity_id for g in gaps} == {"e"}

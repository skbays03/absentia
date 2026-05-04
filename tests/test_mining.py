from lacuna.entities import FeatureSet
from lacuna.mining import mine
from lacuna.selectors import Group


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

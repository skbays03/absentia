from lacuna.entities import Entity, FeatureSet


def test_entity_id_is_qualified_name():
    e = Entity(kind="function", qualified_name="x.py::foo", file_path="x.py", line=1)
    assert e.id == "x.py::foo"


def test_entity_is_hashable_and_comparable():
    a = Entity(kind="function", qualified_name="x.py::foo", file_path="x.py", line=1)
    b = Entity(kind="function", qualified_name="x.py::foo", file_path="x.py", line=1)
    c = Entity(kind="function", qualified_name="x.py::bar", file_path="x.py", line=2)
    assert a == b
    assert a != c
    assert {a, b, c} == {a, c}  # b deduped


def test_featureset_get_set_returns_frozenset_for_missing_kind():
    fs = FeatureSet()
    assert fs.get_set("decorator") == frozenset()
    assert isinstance(fs.get_set("decorator"), frozenset)


def test_featureset_get_set_returns_values_when_present():
    fs = FeatureSet(by_kind={"decorator": frozenset({"@audit", "@route"})})
    assert fs.get_set("decorator") == frozenset({"@audit", "@route"})

from lacuna.entities import Entity, FeatureSet, clean_call_name


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


def test_clean_call_name_passes_through_simple_names():
    assert clean_call_name("foo") == "foo"
    assert clean_call_name("self.update") == "self.update"
    assert clean_call_name("Logger.shared.log") == "Logger.shared.log"


def test_clean_call_name_collapses_inner_parens():
    """Chained calls like ``parse(x).unwrap`` end up with the inner call's
    full text as part of the receiver — collapse to ``(...)``."""
    assert clean_call_name("parse_low_raw(None::<&str>).unwrap") == "parse_low_raw(...).unwrap"
    assert clean_call_name("foo(a, b).bar") == "foo(...).bar"


def test_clean_call_name_handles_nested_parens():
    """Nested generic types or calls inside the receiver."""
    assert clean_call_name("Box::<Vec<u8>>::new(buf).into") == "Box::<Vec<u8>>::new(...).into"


def test_clean_call_name_no_close_paren_left_alone():
    """If parens are unbalanced, return as-is rather than truncating."""
    assert clean_call_name("foo(bar") == "foo(bar"

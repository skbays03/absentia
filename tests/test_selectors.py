from lacuna.entities import Entity, FeatureSet
from lacuna.selectors import decorator_groups, directory_groups, parent_class_groups


def _entity(path: str, name: str) -> Entity:
    return Entity(
        kind="function",
        qualified_name=f"{path}::{name}",
        file_path=path,
        line=1,
    )


def test_directory_groups_groups_by_parent_dir():
    items = [
        (_entity("api/users.py", "create"), FeatureSet()),
        (_entity("api/users.py", "update"), FeatureSet()),
        (_entity("api/orders.py", "refund"), FeatureSet()),
        (_entity("tools/build.py", "main"), FeatureSet()),
    ]
    groups = directory_groups(items, min_members=1)
    by_name = {g.name: g for g in groups}
    assert set(by_name) == {"api", "tools"}
    assert len(by_name["api"].members) == 3
    assert len(by_name["tools"].members) == 1


def test_directory_groups_respects_min_members():
    items = [
        (_entity("api/users.py", f"fn_{i}"), FeatureSet()) for i in range(5)
    ] + [
        (_entity("tools/x.py", "y"), FeatureSet()),
    ]
    groups = directory_groups(items, min_members=3)
    assert {g.name for g in groups} == {"api"}


def test_directory_groups_kind_filter_excludes_other_kinds():
    items = [
        (_entity("a/x.py", "foo"), FeatureSet()),
        (
            Entity(kind="class", qualified_name="a/y.py::Bar", file_path="a/y.py", line=1),
            FeatureSet(),
        ),
        (_entity("a/z.py", "baz"), FeatureSet()),
    ]
    groups = directory_groups(items, min_members=1, kind_filter=("function",))
    [g] = groups
    assert len(g.members) == 2  # the class is excluded


def test_root_directory_is_labeled_root():
    items = [(_entity("foo.py", "bar"), FeatureSet())]
    [g] = directory_groups(items, min_members=1)
    assert g.name == "<root>"


def _features(*decs: str) -> FeatureSet:
    return FeatureSet(by_kind={"decorator": frozenset(decs)})


def test_decorator_groups_one_per_unique_decorator():
    items = [
        (_entity("a.py", "x"), _features("@audit")),
        (_entity("a.py", "y"), _features("@audit", "@route")),
        (_entity("a.py", "z"), _features("@audit", "@route")),
        (_entity("a.py", "w"), _features("@route")),
    ]
    by_name = {g.name: g for g in decorator_groups(items, min_members=1)}
    assert set(by_name) == {"@audit", "@route"}
    assert len(by_name["@audit"].members) == 3
    assert len(by_name["@route"].members) == 3


def test_decorator_groups_excludes_defaults():
    items = [
        (_entity("a.py", "x"), _features("@property")),
        (_entity("a.py", "y"), _features("@property", "@audit")),
        (_entity("a.py", "z"), _features("@audit")),
    ]
    groups = decorator_groups(items, min_members=1)
    assert {g.name for g in groups} == {"@audit"}


def test_decorator_groups_respects_min_members():
    items = [
        (_entity("a.py", "x"), _features("@audit")),
        (_entity("a.py", "y"), _features("@once")),
    ]
    groups = decorator_groups(items, min_members=2)
    assert groups == []


def test_decorator_selector_emits_group_with_correct_type():
    items = [
        (_entity("a.py", "x"), _features("@audit")),
        (_entity("a.py", "y"), _features("@audit")),
    ]
    [g] = decorator_groups(items, min_members=1)
    assert g.selector_type == "decorator"
    assert g.id == "decorator::@audit"


def _class(file_path: str, name: str) -> Entity:
    return Entity(
        kind="class",
        qualified_name=f"{file_path}::{name}",
        file_path=file_path,
        line=1,
    )


def _class_features(*parents: str) -> FeatureSet:
    return FeatureSet(by_kind={"parent_class": frozenset(parents)})


def test_parent_class_groups_one_per_unique_parent():
    items = [
        (_class("a.py", "Apple"),  _class_features("Fruit")),
        (_class("a.py", "Banana"), _class_features("Fruit")),
        (_class("a.py", "Cherry"), _class_features("Fruit", "Stone")),
        (_class("a.py", "Lump"),   _class_features("Stone")),
    ]
    by_name = {g.name: g for g in parent_class_groups(items, min_members=1)}
    assert set(by_name) == {"Fruit", "Stone"}
    assert len(by_name["Fruit"].members) == 3
    assert len(by_name["Stone"].members) == 2


def test_parent_class_groups_excludes_object_by_default():
    items = [
        (_class("a.py", "A"), _class_features("object")),
        (_class("a.py", "B"), _class_features("object", "Mixin")),
        (_class("a.py", "C"), _class_features("Mixin")),
    ]
    groups = parent_class_groups(items, min_members=1)
    assert {g.name for g in groups} == {"Mixin"}


def test_parent_class_groups_skips_non_class_entities():
    items = [
        (_class("a.py", "A"), _class_features("Base")),
        (_class("a.py", "B"), _class_features("Base")),
        (
            Entity(kind="function", qualified_name="a.py::fn", file_path="a.py", line=1),
            FeatureSet(by_kind={"parent_class": frozenset({"Base"})}),
        ),
    ]
    [g] = parent_class_groups(items, min_members=1)
    assert len(g.members) == 2  # the function is not a class member


def test_parent_class_selector_id_format():
    items = [
        (_class("a.py", "A"), _class_features("Base")),
        (_class("a.py", "B"), _class_features("Base")),
    ]
    [g] = parent_class_groups(items, min_members=1)
    assert g.selector_type == "parent_class"
    assert g.id == "parent_class::Base"
    assert g.identity_feature == ("parent_class", "Base")

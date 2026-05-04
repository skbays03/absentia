from lacuna.entities import Entity, FeatureSet
from lacuna.selectors import decorator_groups, directory_groups


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

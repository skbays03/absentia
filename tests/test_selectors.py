from lacuna.entities import Entity, FeatureSet
from lacuna.selectors import directory_groups


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

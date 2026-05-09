"""Tests for src/absentia/enrichment.py."""
from __future__ import annotations

from absentia.entities import Entity, FeatureSet
from absentia.enrichment import (
    build_test_method_index,
    candidate_test_entity_ids,
    enrich_entry_point_registration,
    enrich_sibling_tests,
    is_test_file,
)


def _func(file_path: str, name: str, kind: str = "function") -> Entity:
    return Entity(
        kind=kind,
        qualified_name=f"{file_path}::{name}",
        file_path=file_path,
        line=1,
    )


# ── is_test_file ──────────────────────────────────────────────────────


def test_is_test_file_in_tests_dir():
    assert is_test_file("tests/test_users.py")
    assert is_test_file("tests/api/test_orders.py")
    assert is_test_file("project/tests/test_x.py")


def test_is_test_file_in_test_dir():
    assert is_test_file("test/foo.py")
    assert is_test_file("project/test/foo.py")


def test_is_test_file_jest_style():
    assert is_test_file("__tests__/Component.tsx")
    assert is_test_file("src/components/__tests__/Button.tsx")


def test_is_test_file_test_prefix():
    assert is_test_file("test_helpers.py")
    assert is_test_file("src/test_helpers.py")  # in-tree


def test_is_test_file_test_suffix():
    assert is_test_file("users_test.go")
    assert is_test_file("Component.test.ts")
    assert is_test_file("Component.spec.ts")


def test_is_test_file_negative():
    assert not is_test_file("src/api/users.py")
    assert not is_test_file("README.md")
    assert not is_test_file("docs/guide.md")
    # "test" as substring of unrelated filename — must not match
    assert not is_test_file("src/contests/lib.py")


# ── candidate_test_entity_ids ────────────────────────────────────────


def test_candidates_for_src_layout():
    """src/api/users.py::create → tests/api/test_users.py::test_create"""
    src = _func("src/api/users.py", "create")
    candidates = list(candidate_test_entity_ids(src))
    assert "tests/api/test_users.py::test_create" in candidates


def test_candidates_in_tree_test():
    """src/api/users.py::create → src/api/test_users.py::test_create"""
    src = _func("src/api/users.py", "create")
    candidates = list(candidate_test_entity_ids(src))
    assert "src/api/test_users.py::test_create" in candidates


def test_candidates_flat_tests():
    """src/api/users.py::create → tests/test_users.py::test_create"""
    src = _func("src/api/users.py", "create")
    candidates = list(candidate_test_entity_ids(src))
    assert "tests/test_users.py::test_create" in candidates


def test_candidates_no_src_prefix():
    """api/users.py::create → tests/api/test_users.py::test_create"""
    src = _func("api/users.py", "create")
    candidates = list(candidate_test_entity_ids(src))
    assert "tests/api/test_users.py::test_create" in candidates


def test_candidates_go_suffix_form():
    """Yields the *_test.* sibling form for Go-flavored projects."""
    src = _func("api/users.go", "Create")
    candidates = list(candidate_test_entity_ids(src))
    assert "api/users_test.go::test_Create" in candidates


# ── enrich_sibling_tests integration ─────────────────────────────────


def test_enrich_marks_function_with_matching_test():
    """A function that has a matching test entity gets sibling_test populated."""
    src = _func("src/api/users.py", "create_user")
    test = _func("tests/api/test_users.py", "test_create_user")

    entities = {src.id: src, test.id: test}
    feature_index = {src.id: FeatureSet(), test.id: FeatureSet()}

    enrich_sibling_tests(entities, feature_index)

    fs = feature_index[src.id]
    assert "sibling_test" in fs.by_kind
    assert fs.by_kind["sibling_test"] == frozenset({"sibling test"})


def test_enrich_marks_function_with_no_test_as_empty():
    """A function with no matching test gets an empty set (eligible but gap)."""
    src = _func("src/api/users.py", "delete_user")

    entities = {src.id: src}
    feature_index = {src.id: FeatureSet()}

    enrich_sibling_tests(entities, feature_index)

    fs = feature_index[src.id]
    assert "sibling_test" in fs.by_kind
    assert fs.by_kind["sibling_test"] == frozenset()


def test_enrich_skips_test_files():
    """Test functions themselves don't get the feature (we don't test tests)."""
    test = _func("tests/api/test_users.py", "test_create_user")

    entities = {test.id: test}
    feature_index = {test.id: FeatureSet()}

    enrich_sibling_tests(entities, feature_index)

    assert "sibling_test" not in feature_index[test.id].by_kind


def test_enrich_skips_private_functions():
    """Underscore-prefixed names are skipped (private; usually not separately tested)."""
    src = _func("src/api/users.py", "_internal_helper")

    entities = {src.id: src}
    feature_index = {src.id: FeatureSet()}

    enrich_sibling_tests(entities, feature_index)

    assert "sibling_test" not in feature_index[src.id].by_kind


def test_enrich_skips_non_function_entities():
    """Class entities aren't candidates for the function-level rule."""
    cls = _func("src/api/users.py", "User", kind="class")

    entities = {cls.id: cls}
    feature_index = {cls.id: FeatureSet()}

    enrich_sibling_tests(entities, feature_index)

    assert "sibling_test" not in feature_index[cls.id].by_kind


def test_enrich_creates_feature_set_if_missing():
    """If an entity isn't in feature_index, enrichment still works."""
    src = _func("src/api/users.py", "create_user")
    entities = {src.id: src}
    feature_index: dict = {}  # empty

    enrich_sibling_tests(entities, feature_index)

    assert src.id in feature_index
    assert "sibling_test" in feature_index[src.id].by_kind


# ── Class-method test detection ──────────────────────────────────────


def _class_method(file_path: str, class_name: str, method_name: str) -> Entity:
    return Entity(
        kind="method",
        qualified_name=f"{file_path}::{class_name}.{method_name}",
        file_path=file_path,
        line=1,
    )


def test_enrich_matches_class_method_test():
    """Source function `create_user` is covered by a unittest-style
    class method `TestUsers.test_create_user`."""
    src = _func("src/api/users.py", "create_user")
    test = _class_method("tests/api/test_users.py", "TestUsers", "test_create_user")

    entities = {src.id: src, test.id: test}
    feature_index = {src.id: FeatureSet(), test.id: FeatureSet()}

    enrich_sibling_tests(entities, feature_index)

    fs = feature_index[src.id]
    assert "sibling_test" in fs.by_kind
    assert fs.by_kind["sibling_test"] == frozenset({"sibling test"})


def test_enrich_matches_either_free_function_or_class_method():
    """If both styles exist, the source is still considered covered."""
    src = _func("src/api/users.py", "create_user")
    free = _func("tests/api/test_users.py", "test_create_user")
    cls = _class_method("tests/api/test_users.py", "TestUsers", "test_create_user")

    entities = {src.id: src, free.id: free, cls.id: cls}
    feature_index = {e.id: FeatureSet() for e in (src, free, cls)}

    enrich_sibling_tests(entities, feature_index)
    assert feature_index[src.id].by_kind["sibling_test"] == frozenset({"sibling test"})


def test_enrich_no_match_when_only_unrelated_class_method():
    """A test method named test_create_user in a different test file
    that doesn't match any candidate path shouldn't count."""
    src = _func("src/api/users.py", "create_user")
    # Test in an unrelated location: tests/billing/, not tests/api/
    far_test = _class_method(
        "tests/billing/test_invoices.py", "TestInvoices", "test_create_user",
    )
    entities = {src.id: src, far_test.id: far_test}
    feature_index = {src.id: FeatureSet(), far_test.id: FeatureSet()}

    enrich_sibling_tests(entities, feature_index)
    assert feature_index[src.id].by_kind["sibling_test"] == frozenset()


def test_build_test_method_index_collects_both_styles():
    """Index includes both free-function tests and class-method tests."""
    free = _func("tests/test_users.py", "test_create")
    cls_method = _class_method("tests/test_users.py", "TestUsers", "test_update")
    not_a_test = _func("tests/test_users.py", "make_fixture")  # no test_ prefix
    src = _func("src/api/users.py", "do_thing")  # not in test file

    entities = {e.id: e for e in (free, cls_method, not_a_test, src)}
    index = build_test_method_index(entities)

    assert "tests/test_users.py" in index
    methods = index["tests/test_users.py"]
    assert "test_create" in methods
    assert "test_update" in methods
    assert "make_fixture" not in methods  # filtered: not test_*
    assert "src/api/users.py" not in index  # not a test file


# ── entry-point registration (Item D) ────────────────────────────


def _class(file_path: str, name: str) -> Entity:
    return Entity(
        kind="class",
        qualified_name=f"{file_path}::{name}",
        file_path=file_path,
        line=1,
    )


def test_entry_point_marks_registered_and_unregistered_classes(tmp_path):
    """Two plugin classes in src/plugins/; pyproject.toml registers
    one. Both should get the feature emitted (their directory has
    at least one registration), with values reflecting their
    individual registration status."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "ex"\nversion = "0.1.0"\n\n'
        '[project.entry-points."ex.plugins"]\n'
        'a = "src.plugins.a:Alpha"\n'
    )
    entities = {
        "alpha-id": _class("src/plugins/a.py", "Alpha"),
        "beta-id":  _class("src/plugins/b.py", "Beta"),
        "out-id":   _class("src/other/c.py", "Gamma"),
    }
    for e_id, entity in entities.items():
        entity.id  # noqa: B018 — touch the cached property
        entities[e_id] = entity
    feature_index: dict[str, FeatureSet] = {
        e.id: FeatureSet() for e in entities.values()
    }
    name_keyed = {e.id: e for e in entities.values()}

    enrich_entry_point_registration(name_keyed, feature_index, tmp_path)

    alpha = next(e for e in entities.values() if "Alpha" in e.qualified_name)
    beta = next(e for e in entities.values() if "Beta" in e.qualified_name)
    out = next(e for e in entities.values() if "Gamma" in e.qualified_name)
    assert feature_index[alpha.id].get_set("entry_point_registered") == frozenset({"registered"})
    assert feature_index[beta.id].get_set("entry_point_registered") == frozenset()
    # Class outside the registered directory: feature not emitted at all.
    assert "entry_point_registered" not in feature_index[out.id].by_kind


def test_entry_point_skips_when_pyproject_missing(tmp_path):
    """No pyproject.toml: enrichment is a no-op, never raises."""
    entities = {"x-id": _class("src/p.py", "X")}
    name_keyed = {e.id: e for e in entities.values()}
    feature_index = {e.id: FeatureSet() for e in entities.values()}
    enrich_entry_point_registration(name_keyed, feature_index, tmp_path)
    for fs in feature_index.values():
        assert "entry_point_registered" not in fs.by_kind


def test_entry_point_skips_when_no_entry_points_declared(tmp_path):
    """Project that doesn't use entry-points pays nothing."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "ex"\nversion = "0.1.0"\n'
    )
    entities = {"x-id": _class("src/p.py", "X")}
    name_keyed = {e.id: e for e in entities.values()}
    feature_index = {e.id: FeatureSet() for e in entities.values()}
    enrich_entry_point_registration(name_keyed, feature_index, tmp_path)
    for fs in feature_index.values():
        assert "entry_point_registered" not in fs.by_kind

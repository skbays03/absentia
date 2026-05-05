"""Tests for src/lacuna/enrichment.py."""
from __future__ import annotations

from lacuna.entities import Entity, FeatureSet
from lacuna.enrichment import (
    candidate_test_entity_ids,
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

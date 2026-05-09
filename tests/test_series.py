"""Tests for src/absentia/series.py."""
from __future__ import annotations

from absentia.entities import Entity
from absentia.series import (
    find_letter_series_gaps,
    find_ordinal_series_gaps,
    find_series_gaps,
    find_version_directory_gaps,
)


def _class_with_methods(
    file_path: str, class_name: str, methods: list[str],
) -> dict[str, Entity]:
    """Build a small entity dict with one class and its methods."""
    out: dict[str, Entity] = {}
    cls = Entity(
        kind="class",
        qualified_name=f"{file_path}::{class_name}",
        file_path=file_path,
        line=1,
    )
    out[cls.id] = cls
    for i, m in enumerate(methods, start=2):
        method = Entity(
            kind="method",
            qualified_name=f"{file_path}::{class_name}.{m}",
            file_path=file_path,
            line=i,
        )
        out[method.id] = method
    return out


def _func_in_file(file_path: str, name: str = "fn") -> Entity:
    return Entity(
        kind="function",
        qualified_name=f"{file_path}::{name}",
        file_path=file_path,
        line=1,
    )


def _entities_for_files(*paths: str) -> dict[str, Entity]:
    """Build a dict[entity_id, Entity] with one function per file."""
    out: dict[str, Entity] = {}
    for p in paths:
        e = _func_in_file(p)
        out[e.id] = e
    return out


# ── Single-gap detection ─────────────────────────────────────────────


def test_simple_migration_gap():
    """0001, 0002, 0004 → flag missing 0003."""
    entities = _entities_for_files(
        "migrations/0001_users.py",
        "migrations/0002_orders.py",
        "migrations/0004_inventory.py",
    )

    rules, gaps = find_series_gaps(entities)

    series_rules = [r for r in rules if r.feature_kind == "series"]
    assert len(series_rules) == 1
    assert series_rules[0].feature_value == "0003_*.py"
    assert len(gaps) == 1


def test_multiple_gaps_in_one_cluster():
    """0001, 0005 with min_members=2: flags missing 0002, 0003, 0004."""
    entities = _entities_for_files(
        "migrations/0001_a.py",
        "migrations/0002_b.py",
        "migrations/0005_e.py",
    )
    # Default max_intra_cluster_gap=5 keeps these in one cluster
    rules, gaps = find_series_gaps(entities)
    missing_values = {r.feature_value for r in rules}
    assert "0003_*.py" in missing_values
    assert "0004_*.py" in missing_values


def test_anchor_entity_is_predecessor():
    """The gap points at the existing member just before the missing index."""
    e1 = _func_in_file("migrations/0001_a.py")
    e2 = _func_in_file("migrations/0002_b.py")
    e4 = _func_in_file("migrations/0004_d.py")
    entities = {e.id: e for e in (e1, e2, e4)}

    rules, gaps = find_series_gaps(entities)
    # Missing 0003 → anchor should be 0002
    assert any(g.entity_id == e2.id for g in gaps)


# ── Cluster separation ────────────────────────────────────────────────


def test_outlier_doesnt_create_huge_gap_range():
    """0001, 0002, 0099 with default max_intra_cluster_gap=5
    treats them as TWO clusters; no gaps emitted."""
    entities = _entities_for_files(
        "migrations/0001_a.py",
        "migrations/0002_b.py",
        "migrations/0099_unrelated.py",
    )
    rules, gaps = find_series_gaps(entities)
    series_rules = [r for r in rules if r.feature_kind == "series"]
    # Each cluster has size 2 or 1 — below default min_members=3
    assert series_rules == []
    assert gaps == []


def test_cluster_threshold_can_be_tuned():
    """A larger max_intra_cluster_gap merges otherwise-separate clusters."""
    entities = _entities_for_files(
        "migrations/0001_a.py",
        "migrations/0002_b.py",
        "migrations/0003_c.py",
        "migrations/0010_d.py",
    )
    # Default: 1-3 in cluster, 10 in its own; missing in [1,3] = none
    rules, gaps = find_series_gaps(entities)
    assert gaps == []

    # With max_gap=10, all four are one cluster; range [1,10], missing 4-9
    rules, gaps = find_series_gaps(entities, max_intra_cluster_gap=10)
    missing_values = {r.feature_value for r in rules}
    assert "0004_*.py" in missing_values
    assert "0009_*.py" in missing_values


# ── Edge cases ────────────────────────────────────────────────────────


def test_min_members_filter():
    """A 2-member directory is below min_members=3 and never fires."""
    entities = _entities_for_files(
        "migrations/0001_a.py",
        "migrations/0003_c.py",
    )
    rules, gaps = find_series_gaps(entities)
    assert gaps == []


def test_files_without_leading_digits_ignored():
    """Files not matching ^\\d+ are excluded from series detection."""
    entities = _entities_for_files(
        "migrations/0001_a.py",
        "migrations/0002_b.py",
        "migrations/README.md",
        "migrations/0004_d.py",
    )
    rules, gaps = find_series_gaps(entities)
    # The README is ignored; we still detect missing 0003
    assert any(r.feature_value == "0003_*.py" for r in rules)


def test_different_directories_dont_merge():
    """0001 in dir A and 0002 in dir B are not the same series."""
    entities = _entities_for_files(
        "migrations/A/0001.py",
        "migrations/B/0002.py",
        "migrations/B/0003.py",
        "migrations/B/0005.py",
    )
    rules, gaps = find_series_gaps(entities)
    # Only B has a series; missing 0004
    assert all(r.group_id.endswith("/B") for r in rules)
    assert any(r.feature_value == "0004_*.py" for r in rules)


def test_no_extension_handled():
    """Files without an extension still mine sensibly."""
    entities = _entities_for_files(
        "scripts/01_setup",
        "scripts/02_run",
        "scripts/04_cleanup",
    )
    rules, gaps = find_series_gaps(entities)
    missing_values = {r.feature_value for r in rules}
    assert "03_*" in missing_values  # no extension to add


def test_width_preserved():
    """Width of zero-padding from existing members is reused."""
    entities = _entities_for_files(
        "migrations/000001_a.py",
        "migrations/000002_b.py",
        "migrations/000004_d.py",
    )
    rules, gaps = find_series_gaps(entities)
    assert any(r.feature_value == "000003_*.py" for r in rules)


def test_complete_series_no_gaps():
    """0001, 0002, 0003 contiguous → no gap."""
    entities = _entities_for_files(
        "migrations/0001_a.py",
        "migrations/0002_b.py",
        "migrations/0003_c.py",
    )
    rules, gaps = find_series_gaps(entities)
    assert gaps == []


def test_rule_id_is_stable():
    """Same input → same rule.id (suppression depends on this)."""
    entities = _entities_for_files(
        "migrations/0001_a.py",
        "migrations/0002_b.py",
        "migrations/0004_d.py",
    )
    r1, _ = find_series_gaps(entities)
    r2, _ = find_series_gaps(entities)
    assert sorted(r.id for r in r1) == sorted(r.id for r in r2)


def test_empty_corpus():
    """No entities → no crash, no rules, no gaps."""
    rules, gaps = find_series_gaps({})
    assert rules == []
    assert gaps == []


# ── Letter-series detection (Item E) ────────────────────────────


def test_letter_series_simple_gap():
    """part_a, part_b, part_d → flag missing part_c."""
    entities = _entities_for_files(
        "docs/part_a.md",
        "docs/part_b.md",
        "docs/part_d.md",
    )
    rules, gaps = find_letter_series_gaps(entities)
    assert len(rules) == 1
    assert rules[0].feature_value == "part_c.md"
    assert len(gaps) == 1


def test_letter_series_uppercase_preserves_case():
    """Cluster of upper-case letters renders the missing letter
    upper-case too — Part_A.md / Part_B.md / Part_D.md → Part_C.md."""
    entities = _entities_for_files(
        "docs/Part_A.md",
        "docs/Part_B.md",
        "docs/Part_D.md",
    )
    rules, _ = find_letter_series_gaps(entities)
    assert len(rules) == 1
    assert rules[0].feature_value == "Part_C.md"


def test_letter_series_distinct_prefixes_dont_merge():
    """`lecture_a/b/c` and `chapter_a/b/c` are independent series —
    a gap in one shouldn't borrow from the other."""
    entities = _entities_for_files(
        "docs/lecture_a.md",
        "docs/lecture_b.md",
        "docs/lecture_d.md",
        "docs/chapter_a.md",
        "docs/chapter_b.md",
        "docs/chapter_c.md",
    )
    rules, _ = find_letter_series_gaps(entities)
    values = sorted(r.feature_value for r in rules)
    # Only the lecture_ series has a hole; chapter_ is intact.
    assert values == ["lecture_c.md"]


def test_letter_series_outlier_doesnt_create_huge_range():
    """`part_a/b/c` plus a stray `part_z.md` — `part_z` is too
    far away to be in the same cluster, so we don't flag d-y as
    24 missing letters."""
    entities = _entities_for_files(
        "docs/part_a.md",
        "docs/part_b.md",
        "docs/part_c.md",
        "docs/part_z.md",
    )
    rules, _ = find_letter_series_gaps(entities)
    assert rules == []


def test_letter_series_too_few_members_skipped():
    """Two letters aren't a "series" — coincidence too easy."""
    entities = _entities_for_files(
        "docs/part_a.md",
        "docs/part_c.md",
    )
    rules, _ = find_letter_series_gaps(entities)
    assert rules == []


def test_letter_series_empty_corpus():
    rules, gaps = find_letter_series_gaps({})
    assert rules == []
    assert gaps == []


# ── Version-directory series (Item F) ─────────────────────────────


def test_version_dir_simple_gap():
    """src/api_v1/, src/api_v2/, src/api_v4/ → flag missing api_v3."""
    entities = _entities_for_files(
        "src/api_v1/users.py",
        "src/api_v2/users.py",
        "src/api_v4/users.py",
    )
    rules, gaps = find_version_directory_gaps(entities)
    assert len(rules) == 1
    assert rules[0].feature_value == "api_v3"
    assert len(gaps) == 1


def test_version_dir_uppercase_v_preserved():
    """``apiV1`` / ``apiV2`` / ``apiV4`` keeps the capital V on the
    rendered missing version."""
    entities = _entities_for_files(
        "src/apiV1/users.py",
        "src/apiV2/users.py",
        "src/apiV4/users.py",
    )
    rules, _ = find_version_directory_gaps(entities)
    assert len(rules) == 1
    assert rules[0].feature_value == "apiV3"


def test_version_dir_zero_padded_width_preserved():
    """``schema_v01`` / ``v02`` / ``v04`` renders ``schema_v03``,
    not ``schema_v3`` — width comes from the cluster's max width."""
    entities = _entities_for_files(
        "src/schema_v01/types.py",
        "src/schema_v02/types.py",
        "src/schema_v04/types.py",
    )
    rules, _ = find_version_directory_gaps(entities)
    assert len(rules) == 1
    assert rules[0].feature_value == "schema_v03"


def test_version_dir_outlier_doesnt_create_huge_range():
    """``v1`` / ``v2`` / ``v3`` plus a stray ``v99`` — too far away
    to be in the same cluster, so we don't flag v4-v98."""
    entities = _entities_for_files(
        "src/api_v1/x.py",
        "src/api_v2/x.py",
        "src/api_v3/x.py",
        "src/api_v99/x.py",
    )
    rules, _ = find_version_directory_gaps(entities)
    assert rules == []


def test_version_dir_two_members_skipped():
    """Two version directories aren't a series — coincidence too easy."""
    entities = _entities_for_files(
        "src/api_v1/x.py",
        "src/api_v3/x.py",
    )
    rules, _ = find_version_directory_gaps(entities)
    assert rules == []


def test_version_dir_distinct_prefixes_dont_merge():
    """``api_v1/2/3`` and ``schema_v1/2/4`` are independent series;
    only the schema one has a hole."""
    entities = _entities_for_files(
        "src/api_v1/x.py",
        "src/api_v2/x.py",
        "src/api_v3/x.py",
        "src/schema_v1/x.py",
        "src/schema_v2/x.py",
        "src/schema_v4/x.py",
    )
    rules, _ = find_version_directory_gaps(entities)
    values = sorted(r.feature_value for r in rules)
    assert values == ["schema_v3"]


def test_version_dir_empty_corpus():
    rules, gaps = find_version_directory_gaps({})
    assert rules == []
    assert gaps == []


# ── Ordinal series detection (Item G) ────────────────────────────


def test_ordinal_series_crud_with_test_prefix():
    """TestUserCRUD has test_create / test_read / test_update —
    flag the missing test_delete."""
    entities = _class_with_methods(
        "tests/test_users.py", "TestUserCRUD",
        ["test_create", "test_read", "test_update"],
    )
    rules, gaps = find_ordinal_series_gaps(entities)
    assert len(rules) == 1
    assert rules[0].feature_value == "test_delete"
    assert len(gaps) == 1


def test_ordinal_series_bare_crud_no_prefix():
    """Plain create/read/update on a class — flag missing delete
    without any prefix."""
    entities = _class_with_methods(
        "src/repo.py", "UserRepo",
        ["create", "read", "update"],
    )
    rules, _ = find_ordinal_series_gaps(entities)
    assert len(rules) == 1
    assert rules[0].feature_value == "delete"


def test_ordinal_series_init_run_close_triple():
    """Three-element ordinal: init/run/close. A class with
    init + run but no close is missing the third slot — same
    pattern as CRUD, just with three members instead of four."""
    entities = _class_with_methods(
        "src/job.py", "Job",
        ["init", "run"],
    )
    rules, _ = find_ordinal_series_gaps(entities)
    close_rules = [r for r in rules if r.feature_value == "close"]
    assert len(close_rules) == 1


def test_ordinal_series_too_few_present_doesnt_fire():
    """Only 1 of 4 CRUD members present — not enough signal to
    claim "this class is implementing CRUD." The 75% threshold
    keeps coincidental name overlaps from triggering."""
    entities = _class_with_methods(
        "src/x.py", "X",
        ["create", "render", "compute", "validate"],
    )
    rules, _ = find_ordinal_series_gaps(entities)
    crud_rules = [r for r in rules if "delete" in r.feature_value]
    assert crud_rules == []


def test_ordinal_series_complete_alphabet_doesnt_fire():
    """All four CRUD members present — no gap to flag."""
    entities = _class_with_methods(
        "src/repo.py", "Repo",
        ["create", "read", "update", "delete"],
    )
    rules, _ = find_ordinal_series_gaps(entities)
    crud_rules = [r for r in rules if r.feature_value in
                  ("create", "read", "update", "delete")]
    assert crud_rules == []


def test_ordinal_series_two_missing_doesnt_fire():
    """Detector only fires on exactly-one-missing. Two missing
    → ambiguous: is this a partial implementation, or just a
    class with a coincidentally-named method?"""
    entities = _class_with_methods(
        "src/x.py", "X",
        ["create", "read"],  # missing 2 of 4
    )
    rules, _ = find_ordinal_series_gaps(entities)
    crud_rules = [r for r in rules if r.feature_value == "delete"]
    assert crud_rules == []


def test_ordinal_series_empty_corpus():
    rules, gaps = find_ordinal_series_gaps({})
    assert rules == []
    assert gaps == []

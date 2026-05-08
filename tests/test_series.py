"""Tests for src/absentia/series.py."""
from __future__ import annotations

from absentia.entities import Entity
from absentia.series import find_series_gaps


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

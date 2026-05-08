"""Unit tests for the Storage layer."""
from __future__ import annotations

import pytest

from absentia.entities import Entity, FeatureSet
from absentia.storage import (
    SCHEMA_VERSION,
    StateLock,
    StateLockError,
    Storage,
    StorageVersionError,
)


def _e(name: str, file_path: str = "x.py", line: int = 1) -> Entity:
    return Entity(
        kind="function",
        qualified_name=f"{file_path}::{name}",
        file_path=file_path,
        line=line,
    )


def test_storage_initializes_state_dir_and_version_file(tmp_path):
    state = tmp_path / ".absentia"
    with Storage(state):
        pass
    assert state.is_dir()
    assert (state / "version").read_text().strip() == str(SCHEMA_VERSION)
    assert (state / "state.db").is_file()


def test_storage_rejects_incompatible_version(tmp_path):
    state = tmp_path / ".absentia"
    state.mkdir()
    (state / "version").write_text("999\n")
    with pytest.raises(StorageVersionError):
        Storage(state)


def test_file_hash_round_trip(tmp_path):
    with Storage(tmp_path / ".absentia") as storage:
        run_id = storage.begin_run()
        assert storage.file_hash("x.py") is None
        storage.upsert_file("x.py", "abc123", run_id)
        storage.commit()
        assert storage.file_hash("x.py") == "abc123"

        # Update existing
        storage.upsert_file("x.py", "def456", run_id)
        storage.commit()
        assert storage.file_hash("x.py") == "def456"


def test_save_and_load_entities_and_features(tmp_path):
    with Storage(tmp_path / ".absentia") as storage:
        run_id = storage.begin_run()
        storage.upsert_file("x.py", "abc", run_id)
        e1 = _e("foo")
        e2 = _e("bar", line=10)
        entities = {e1.id: e1, e2.id: e2}
        features = {
            e1.id: FeatureSet(by_kind={
                "decorator": frozenset({"@audit", "@route"}),
                "calls": frozenset({"helper"}),
            }),
            e2.id: FeatureSet(),
        }
        storage.save_entities_and_features(entities, features)
        storage.commit()

    # Reopen and read back
    with Storage(tmp_path / ".absentia") as storage:
        loaded_entities, loaded_features = storage.load_all()
        assert loaded_entities == entities
        assert loaded_features[e1.id].get_set("decorator") == frozenset(
            {"@audit", "@route"}
        )
        assert loaded_features[e1.id].get_set("calls") == frozenset({"helper"})
        assert loaded_features[e2.id].by_kind == {}


def test_delete_entities_for_file_removes_features_too(tmp_path):
    with Storage(tmp_path / ".absentia") as storage:
        run_id = storage.begin_run()
        storage.upsert_file("a.py", "h1", run_id)
        e = _e("foo", file_path="a.py")
        storage.save_entities_and_features(
            {e.id: e},
            {e.id: FeatureSet(by_kind={"decorator": frozenset({"@x"})})},
        )
        storage.commit()
        loaded_e, loaded_f = storage.load_all()
        assert loaded_e
        assert loaded_f[e.id].get_set("decorator")

        storage.delete_entities_for_file("a.py")
        storage.commit()
        loaded_e2, loaded_f2 = storage.load_all()
        assert loaded_e2 == {}
        assert loaded_f2 == {}


def test_delete_file_clears_records(tmp_path):
    with Storage(tmp_path / ".absentia") as storage:
        run_id = storage.begin_run()
        storage.upsert_file("a.py", "h1", run_id)
        e = _e("foo", file_path="a.py")
        storage.save_entities_and_features(
            {e.id: e}, {e.id: FeatureSet()}
        )
        storage.commit()

        storage.delete_file("a.py")
        storage.commit()
        assert storage.file_hash("a.py") is None
        loaded_e, _ = storage.load_all()
        assert loaded_e == {}


def test_runs_table_records_lifecycle(tmp_path):
    with Storage(tmp_path / ".absentia") as storage:
        run_id = storage.begin_run()
        assert run_id == 1
        storage.end_run(run_id, duration_ms=42.0, entities_scanned=10,
                        rules_discovered=3, gaps_found=1)
        row = storage.conn.execute(
            "SELECT duration_ms, entities_scanned, rules_discovered, gaps_found "
            "FROM runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        assert row == (42.0, 10, 3, 1)


def test_all_file_hashes_returns_dict(tmp_path):
    with Storage(tmp_path / ".absentia") as storage:
        run_id = storage.begin_run()
        storage.upsert_file("a.py", "h1", run_id)
        storage.upsert_file("b.py", "h2", run_id)
        storage.commit()
        assert storage.all_file_hashes() == {"a.py": "h1", "b.py": "h2"}


# ── StateLock ────────────────────────────────────────────────────────


def test_state_lock_acquires_and_releases(tmp_path):
    lockfile = tmp_path / ".absentia" / "lockfile"
    with StateLock(lockfile):
        assert lockfile.exists()
    # After release, can acquire again
    with StateLock(lockfile):
        pass


def test_state_lock_rejects_concurrent_acquire(tmp_path):
    lockfile = tmp_path / ".absentia" / "lockfile"
    outer = StateLock(lockfile)
    outer.__enter__()
    try:
        with pytest.raises(StateLockError):
            with StateLock(lockfile):
                pass
    finally:
        outer.__exit__(None, None, None)


def test_state_lock_can_be_reacquired_after_release(tmp_path):
    lockfile = tmp_path / ".absentia" / "lockfile"
    with StateLock(lockfile):
        pass
    with StateLock(lockfile):
        pass
    with StateLock(lockfile):
        pass

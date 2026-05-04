"""SQLite persistence for lacuna's per-repo state.

The Storage class manages everything in ``.lacuna/state.db``: file
content hashes (the basis for incremental mining), entities, features,
and run history. Rules and gaps are *not* persisted — mining is fast
enough to recompute every run. That keeps the schema small.

StateLock guards concurrent invocations via fcntl on a sentinel file
in the state directory.

The state directory also contains:

- ``version`` — schema version (plain text, redundant with PRAGMA
  user_version inside state.db)
- ``last_run.json`` — quick-read summary of the most recent run
- ``lockfile`` — fcntl-locked while a lacuna instance is active
"""
from __future__ import annotations

import fcntl
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .entities import Entity, FeatureSet


SCHEMA_VERSION = 1


_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    last_run_id INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_run ON files(last_run_id);

CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    line INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entities_file ON entities(file_path);

CREATE TABLE IF NOT EXISTS features (
    entity_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (entity_id, kind)
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    duration_ms REAL,
    entities_scanned INTEGER,
    rules_discovered INTEGER,
    gaps_found INTEGER
);
"""


class StorageVersionError(Exception):
    """Raised when on-disk state was written by an incompatible schema."""


class Storage:
    """Per-repo SQLite-backed state.

    Use as a context manager so the connection closes deterministically::

        with Storage(state_dir) as storage:
            ...
    """

    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.state_dir.mkdir(exist_ok=True)
        self.db_path = state_dir / "state.db"
        self._check_version_file()
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._apply_schema()

    # ── Lifecycle ────────────────────────────────────────────────────

    def _check_version_file(self) -> None:
        version_file = self.state_dir / "version"
        if version_file.exists():
            try:
                on_disk = int(version_file.read_text().strip())
            except ValueError:
                on_disk = -1
            if on_disk != SCHEMA_VERSION:
                raise StorageVersionError(
                    f"State at {self.state_dir} was written with schema v{on_disk}; "
                    f"this lacuna binary expects v{SCHEMA_VERSION}. "
                    f"Delete the .lacuna/ directory to rebuild from scratch."
                )
        else:
            version_file.write_text(f"{SCHEMA_VERSION}\n")

    def _apply_schema(self) -> None:
        self.conn.executescript(_SCHEMA)
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def commit(self) -> None:
        self.conn.commit()

    # ── File hashes ──────────────────────────────────────────────────

    def file_hash(self, path: str) -> str | None:
        row = self.conn.execute(
            "SELECT content_hash FROM files WHERE path = ?", (path,)
        ).fetchone()
        return row[0] if row else None

    def all_file_hashes(self) -> dict[str, str]:
        return dict(self.conn.execute("SELECT path, content_hash FROM files").fetchall())

    def upsert_file(self, path: str, content_hash: str, run_id: int) -> None:
        self.conn.execute(
            "INSERT INTO files (path, content_hash, last_run_id) VALUES (?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET "
            "  content_hash = excluded.content_hash, "
            "  last_run_id  = excluded.last_run_id",
            (path, content_hash, run_id),
        )

    def delete_file(self, path: str) -> None:
        """Remove a file's record AND all its entities/features."""
        self.delete_entities_for_file(path)
        self.conn.execute("DELETE FROM files WHERE path = ?", (path,))

    # ── Entities + features ──────────────────────────────────────────

    def delete_entities_for_file(self, path: str) -> None:
        self.conn.execute(
            "DELETE FROM features WHERE entity_id IN ("
            "  SELECT id FROM entities WHERE file_path = ?)",
            (path,),
        )
        self.conn.execute("DELETE FROM entities WHERE file_path = ?", (path,))

    def save_entities_and_features(
        self,
        entities: dict[str, Entity],
        features: dict[str, FeatureSet],
    ) -> None:
        for entity in entities.values():
            self.conn.execute(
                "INSERT OR REPLACE INTO entities "
                "(id, kind, qualified_name, file_path, line) VALUES (?, ?, ?, ?, ?)",
                (entity.id, entity.kind, entity.qualified_name,
                 entity.file_path, entity.line),
            )
        for entity_id, fset in features.items():
            for kind, value in fset.by_kind.items():
                if isinstance(value, (set, frozenset)):
                    encoded = json.dumps(sorted(value))
                else:
                    encoded = json.dumps(value)
                self.conn.execute(
                    "INSERT OR REPLACE INTO features (entity_id, kind, value) "
                    "VALUES (?, ?, ?)",
                    (entity_id, kind, encoded),
                )

    def load_all(self) -> tuple[dict[str, Entity], dict[str, FeatureSet]]:
        entities: dict[str, Entity] = {}
        features: dict[str, FeatureSet] = {}

        for row in self.conn.execute(
            "SELECT id, kind, qualified_name, file_path, line FROM entities"
        ):
            e = Entity(
                kind=row[1], qualified_name=row[2], file_path=row[3], line=row[4]
            )
            entities[e.id] = e

        for entity_id, kind, value_json in self.conn.execute(
            "SELECT entity_id, kind, value FROM features"
        ):
            if entity_id not in features:
                features[entity_id] = FeatureSet()
            decoded = json.loads(value_json)
            if isinstance(decoded, list):
                features[entity_id].by_kind[kind] = frozenset(decoded)
            else:
                features[entity_id].by_kind[kind] = decoded

        # Every entity gets a FeatureSet, even if empty.
        for eid in entities:
            features.setdefault(eid, FeatureSet())

        return entities, features

    # ── Runs ─────────────────────────────────────────────────────────

    def begin_run(self) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (started_at) VALUES (?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
        run_id = cur.lastrowid
        assert run_id is not None
        self.conn.commit()
        return run_id

    def end_run(
        self,
        run_id: int,
        *,
        duration_ms: float,
        entities_scanned: int,
        rules_discovered: int,
        gaps_found: int,
    ) -> None:
        self.conn.execute(
            "UPDATE runs SET "
            "  duration_ms = ?, entities_scanned = ?, "
            "  rules_discovered = ?, gaps_found = ? "
            "WHERE id = ?",
            (duration_ms, entities_scanned, rules_discovered, gaps_found, run_id),
        )
        self.conn.commit()


# ── Lockfile ─────────────────────────────────────────────────────────


class StateLockError(Exception):
    """Raised when another process holds the state lock."""


class StateLock:
    """Cross-process exclusion via ``fcntl.flock`` on a sentinel file.

    Use as a context manager. Non-blocking acquire — if another lacuna
    instance is already holding the lock, raises ``StateLockError``
    immediately rather than waiting.

    Unix-only. Windows support (portalocker) is post-MVP.
    """

    def __init__(self, lockfile_path: Path):
        self.lockfile_path = lockfile_path
        self._fd: int | None = None

    def __enter__(self) -> "StateLock":
        self.lockfile_path.parent.mkdir(exist_ok=True)
        self.lockfile_path.touch()
        self._fd = os.open(self.lockfile_path, os.O_RDWR)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(self._fd)
            self._fd = None
            raise StateLockError(
                f"another lacuna instance is running on this repo "
                f"(lockfile: {self.lockfile_path})"
            ) from exc
        return self

    def __exit__(self, *_: object) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None

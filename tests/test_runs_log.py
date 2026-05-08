"""Tests for src/absentia/runs_log.py."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from absentia.runs_log import (
    MIN_RUNS_FOR_AGGREGATION,
    RUNS_LOG_FILENAME,
    aggregate,
    append_run,
    load_recent_runs,
    runs_log_path,
)


def _make_run(
    *,
    ts: datetime | str | None = None,
    version: str = "0.1.0",
    cores: int = 8,
    jobs: int = 4,
    root: str = "/tmp/foo",
    bytes_per_lang: dict[str, int] | None = None,
    mine_ms: float = 100.0,
    finalize_ms: float = 5.0,
    parse_ms: float = 50.0,
    walk_ms: float = 10.0,
    store_ms: float = 5.0,
) -> dict:
    """Build a runs.jsonl-shaped dict for testing."""
    if ts is None:
        ts = datetime.now(timezone.utc)
    if isinstance(ts, datetime):
        ts = ts.isoformat()
    return {
        "ts": ts,
        "version": version,
        "cores": cores,
        "jobs": jobs,
        "root": root,
        "files": 100,
        "files_unchanged": 0,
        "entities": 1000,
        "by_language_bytes": bytes_per_lang or {"python": 1_000_000},
        "stage_ms": {
            "walk": walk_ms,
            "parse": parse_ms,
            "store": store_ms,
            "mine": mine_ms,
            "finalize": finalize_ms,
        },
        "gaps": 5,
    }


def test_runs_log_path_is_under_home() -> None:
    p = runs_log_path()
    assert p.name == RUNS_LOG_FILENAME
    assert p.parent.name == ".absentia"
    assert str(p).startswith(str(Path.home()))


def test_append_and_load_roundtrip(tmp_path: Path) -> None:
    log = tmp_path / "runs.jsonl"
    rec = _make_run(root="/proj/a")
    append_run(rec, path=log)
    loaded = load_recent_runs(log)
    assert len(loaded) == 1
    assert loaded[0]["root"] == "/proj/a"
    assert loaded[0]["stage_ms"]["mine"] == 100.0


def test_append_creates_parent_dir(tmp_path: Path) -> None:
    log = tmp_path / "deep" / "nested" / "runs.jsonl"
    append_run(_make_run(), path=log)
    assert log.exists()


def test_append_failure_is_silent(tmp_path: Path) -> None:
    # Path that points to a file (not a dir) — making the parent
    # mkdir would fail. append_run should swallow the error.
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("blocking file")
    log = blocker / "runs.jsonl"  # blocker can't be a parent
    append_run(_make_run(), path=log)  # must not raise


def test_load_skips_malformed_lines(tmp_path: Path) -> None:
    log = tmp_path / "runs.jsonl"
    log.write_text(
        json.dumps(_make_run()) + "\n"
        "not a json line\n"
        + json.dumps(_make_run(root="/proj/b")) + "\n"
        "\n"  # blank line tolerated
    )
    loaded = load_recent_runs(log)
    assert len(loaded) == 2
    assert loaded[1]["root"] == "/proj/b"


def test_load_filters_by_age(tmp_path: Path) -> None:
    log = tmp_path / "runs.jsonl"
    old = _make_run(ts=datetime.now(timezone.utc) - timedelta(days=200))
    fresh = _make_run(ts=datetime.now(timezone.utc) - timedelta(days=5))
    log.write_text(json.dumps(old) + "\n" + json.dumps(fresh) + "\n")
    loaded = load_recent_runs(log, max_age_days=90)
    assert len(loaded) == 1
    # Only the fresh one survives
    assert loaded[0]["ts"] == fresh["ts"]


def test_load_returns_empty_when_missing(tmp_path: Path) -> None:
    log = tmp_path / "no-such-file.jsonl"
    assert load_recent_runs(log) == []


def test_aggregate_below_threshold_returns_empty_metrics() -> None:
    runs = [_make_run() for _ in range(MIN_RUNS_FOR_AGGREGATION - 1)]
    agg = aggregate(runs, current_cores=8, current_version="0.1.0")
    assert agg.runs_used == MIN_RUNS_FOR_AGGREGATION - 1
    assert agg.mining_seconds_per_byte is None  # not enough samples


def test_aggregate_filters_incompatible_runs() -> None:
    # 2 compatible + 2 with wrong cores + 1 with wrong version
    runs = [
        _make_run(cores=8, version="0.1.0"),
        _make_run(cores=8, version="0.1.0"),
        _make_run(cores=4, version="0.1.0"),  # different cores
        _make_run(cores=4, version="0.1.0"),  # different cores
        _make_run(cores=8, version="0.0.9"),  # different version
    ]
    agg = aggregate(runs, current_cores=8, current_version="0.1.0")
    assert agg.runs_used == 2
    assert agg.runs_skipped == 3
    assert agg.mining_seconds_per_byte is None  # below threshold even after filter


def test_aggregate_computes_mining_throughput() -> None:
    # 3 runs, each with 1MB bytes and 100ms mine + 5ms finalize.
    # Total: 3MB / 0.315s = ~9.5 MB/s mining → 1.05e-7 s/byte.
    runs = [_make_run() for _ in range(3)]
    agg = aggregate(runs, current_cores=8, current_version="0.1.0")
    assert agg.runs_used == 3
    assert agg.mining_seconds_per_byte is not None
    expected = (3 * 0.105) / (3 * 1_000_000)  # 105ms / 1MB
    assert abs(agg.mining_seconds_per_byte - expected) < 1e-9


def test_aggregate_distinct_corpora() -> None:
    runs = [
        _make_run(root="/proj/a"),
        _make_run(root="/proj/a"),
        _make_run(root="/proj/b"),
    ]
    agg = aggregate(runs, current_cores=8, current_version="0.1.0")
    assert sorted(agg.distinct_corpora) == ["/proj/a", "/proj/b"]


def test_aggregate_skips_runs_with_zero_bytes() -> None:
    # A row with no bytes shouldn't poison the throughput math.
    runs = [
        _make_run(),
        _make_run(),
        _make_run(bytes_per_lang={}, mine_ms=0, finalize_ms=0),
        _make_run(),
    ]
    agg = aggregate(runs, current_cores=8, current_version="0.1.0")
    assert agg.runs_used == 4
    assert agg.mining_seconds_per_byte is not None
    # The 3-bytes run shouldn't be in the numerator/denominator
    expected = (3 * 0.105) / (3 * 1_000_000)
    assert abs(agg.mining_seconds_per_byte - expected) < 1e-9


def test_aggregate_no_filter_when_args_none() -> None:
    # Passing None for current_cores/version disables filtering — all
    # rows count. Useful for "show me everything" callers.
    runs = [
        _make_run(cores=8),
        _make_run(cores=4),
        _make_run(cores=2),
    ]
    agg = aggregate(runs, current_cores=None, current_version=None)
    assert agg.runs_used == 3
    assert agg.runs_skipped == 0

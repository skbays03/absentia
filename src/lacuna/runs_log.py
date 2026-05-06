"""Per-run timing log.

Every successful ``lacuna check`` appends a JSON line to
``~/.lacuna/runs.jsonl``. ``lacuna est`` aggregates the rows to refine
its predictions over time — the more often you run check, the more
accurate the estimate becomes, with no explicit recalibration step.

Privacy: the log is machine-local. It records the scanned root path,
language-byte shape, and stage timings. Nothing leaves the machine.
Users can ``rm ~/.lacuna/runs.jsonl`` at any time to start fresh.

Schema (one JSON object per line):

    {
      "ts": "2026-05-06T03:20:11.000+00:00",
      "version": "0.0.1",
      "cores": 10,
      "jobs": 5,
      "root": "/home/user/linux",
      "files": 65016,
      "files_unchanged": 0,
      "entities": 612443,
      "by_language_bytes": {"c": 800_000_000, "python": 50_000, ...},
      "stage_ms": {"walk": 1200, "parse": 21000, "store": 500,
                   "mine": 444600, "finalize": 700}
    }

Append-only. Forward-compatible: future fields can be added without
breaking older code; missing fields fall through with sensible
defaults during aggregation.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path


RUNS_LOG_FILENAME = "runs.jsonl"

# Drop runs older than this when aggregating. 90 days matches the
# stale-calibration window — old enough to show seasonal variance,
# fresh enough that hardware changes invalidate cleanly.
RECENT_RUNS_DAYS = 90

# Cap how many we load to keep memory bounded on long-lived users.
# 200 runs at typical row size ≈ 80-200 KB; trivial.
MAX_RUNS_LOADED = 200

# Threshold below which we don't trust aggregated values yet — fall
# back to the static calibration. 3 runs is enough to smooth out
# obvious one-off noise (a check during heavy CPU contention).
MIN_RUNS_FOR_AGGREGATION = 3


def runs_log_path() -> Path:
    """Default location: ``~/.lacuna/runs.jsonl``."""
    return Path.home() / ".lacuna" / RUNS_LOG_FILENAME


def append_run(record: dict, path: Path | None = None) -> None:
    """Append one record. Best-effort — disk-full or read-only home
    fails silently rather than breaking a successful scan."""
    p = path if path is not None else runs_log_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as fh:
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")
    except OSError:
        pass


def load_recent_runs(
    path: Path | None = None,
    *,
    max_age_days: int = RECENT_RUNS_DAYS,
    max_count: int = MAX_RUNS_LOADED,
) -> list[dict]:
    """Read recent run records, oldest-first. Filters by age and caps
    by count. Malformed lines are skipped silently."""
    p = path if path is not None else runs_log_path()
    if not p.exists():
        return []

    rows: list[dict] = []
    try:
        with p.open() as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    obj = json.loads(stripped)
                    if isinstance(obj, dict):
                        rows.append(obj)
                except ValueError:
                    continue
    except OSError:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    fresh: list[dict] = []
    for r in rows:
        ts_str = r.get("ts")
        if not isinstance(ts_str, str):
            continue
        try:
            t = datetime.fromisoformat(ts_str)
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if t >= cutoff:
            fresh.append(r)

    return fresh[-max_count:]


@dataclass(frozen=True)
class AggregatedTimings:
    """Aggregate timings derived from a window of recent runs.

    Fields are populated only when there's enough signal — empty /
    None values mean "fall back to static calibration."
    """
    runs_used: int = 0
    runs_skipped: int = 0
    mining_seconds_per_byte: float | None = None
    # The set of corpus paths represented in the aggregation, useful
    # for the --history view and for the confidence reason ("based
    # on 5 runs across 3 corpora").
    distinct_corpora: tuple[str, ...] = field(default_factory=tuple)


def aggregate(
    runs: list[dict],
    *,
    current_cores: int | None = None,
    current_version: str | None = None,
) -> AggregatedTimings:
    """Aggregate a list of run records into a refined timing model.

    Filters runs whose ``cores`` or ``version`` fields disagree with
    the current machine — those are likely stale data from a previous
    laptop or a pre-upgrade install and would skew the estimate.

    Mining throughput is the only metric we aggregate currently:
    every run produces a clean mine+finalize / corpus_bytes data
    point regardless of cache state, since mining always runs on the
    full entity set. Per-language parse bps is harder (you can't
    cleanly attribute parse wall-time to one language in a
    multi-language run), so that stays calibration-derived for now.
    """
    compat: list[dict] = []
    skipped = 0
    for r in runs:
        if current_cores is not None and r.get("cores") != current_cores:
            skipped += 1
            continue
        if current_version is not None and r.get("version") != current_version:
            skipped += 1
            continue
        compat.append(r)

    if len(compat) < MIN_RUNS_FOR_AGGREGATION:
        return AggregatedTimings(
            runs_used=len(compat), runs_skipped=skipped,
            distinct_corpora=tuple(sorted({
                r.get("root", "?") for r in compat if r.get("root")
            })),
        )

    # Mining throughput across all compatible runs. We sum the
    # numerator and denominator separately rather than averaging
    # per-run rates — that way a tiny corpus contributing noisy
    # data doesn't move the mean as much as a large clean run.
    total_mine_ms = 0.0
    total_bytes = 0
    for r in compat:
        stage = r.get("stage_ms") or {}
        mine_ms = float(stage.get("mine") or 0)
        final_ms = float(stage.get("finalize") or 0)
        by_lang = r.get("by_language_bytes") or {}
        bytes_n = sum(int(v or 0) for v in by_lang.values())
        if bytes_n > 0 and (mine_ms + final_ms) > 0:
            total_mine_ms += mine_ms + final_ms
            total_bytes += bytes_n

    mining_spb: float | None = None
    if total_bytes > 0 and total_mine_ms > 0:
        mining_spb = (total_mine_ms / 1000.0) / total_bytes

    return AggregatedTimings(
        runs_used=len(compat),
        runs_skipped=skipped,
        mining_seconds_per_byte=mining_spb,
        distinct_corpora=tuple(sorted({
            r.get("root", "?") for r in compat if r.get("root")
        })),
    )

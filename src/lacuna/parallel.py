"""Parallel parsing helpers.

Lacuna's hot loop is parse + extract, which is per-file independent.
Running that stage across a pool of worker processes cuts wall-clock
substantially on large corpora (Linux kernel: ~96s → ~30s on an
8-core M-series at default jobs). Storage writes stay on the main
process — SQLite is single-writer; sharing a connection across
processes only serializes the writes anyway.

The default worker count is half of detected CPU cores. A developer
machine is typically running an IDE, browser, and chat tools at the
same time; saturating all cores during a rescan stutters the rest of
the system. Power users opt into the full machine with ``--jobs N``.
"""
from __future__ import annotations

import os
from typing import Any

from .entities import Entity, FeatureSet


def detected_cores() -> int:
    """Cores reported by the OS, minimum 1.

    Uses ``os.process_cpu_count()`` (3.13+, respects cgroup CPU limits
    in containers); returns 1 if it yields no value.
    """
    n = os.process_cpu_count()
    if not n or n < 1:
        return 1
    return n


def default_jobs() -> int:
    """User-set override (``settings.json :: jobs_default``) if present;
    otherwise half of detected cores, rounded down, minimum 1.

    The user can pin a default via ``lacuna --jobs-default N``; that
    value wins over the auto half-cores heuristic. Per-invocation
    ``check --jobs N`` always overrides both.
    """
    from .settings import load_settings
    s = load_settings()
    if s.jobs_default is not None and s.jobs_default >= 1:
        return s.jobs_default
    return max(1, detected_cores() // 2)


# Threshold: skip the pool entirely if there are fewer changed files
# than this many per worker. The fixed cost of spawning workers and
# pickling results back outweighs the parallelism gain on small jobs.
_MIN_FILES_PER_WORKER = 4


def should_parallelize(num_changed_files: int, jobs: int) -> bool:
    """True iff there's enough work to justify the worker startup cost."""
    if jobs <= 1:
        return False
    return num_changed_files >= jobs * _MIN_FILES_PER_WORKER


# Process-local cache: workers reuse Extractor instances across files
# to avoid re-loading tree-sitter grammars per call. Lives in the
# worker process only; never shared back to the main process.
_WORKER_EXTRACTORS: dict[str, Any] = {}


def _worker_get_extractor(language_name: str) -> Any:
    if language_name not in _WORKER_EXTRACTORS:
        from .extractors import discover_extractors
        _WORKER_EXTRACTORS.update(discover_extractors([language_name]))
    return _WORKER_EXTRACTORS[language_name]


def parse_one(
    args: tuple[str, bytes, str],
) -> tuple[str, list[tuple[Entity, FeatureSet]]]:
    """Worker entry point: parse + extract one file.

    Top-level so it pickles for ``ProcessPoolExecutor``. Takes
    ``(relative_path, content_bytes, language_name)`` and returns
    ``(relative_path, list[(Entity, FeatureSet)])``. The language_name
    is what tells the worker which extractor to use — extractor
    instances themselves don't pickle cleanly (tree-sitter Parser
    objects hold C state), so we re-resolve in-process.
    """
    rel, content, language_name = args
    extractor = _worker_get_extractor(language_name)
    tree_root = extractor.parse(content)
    return rel, list(extractor.extract(tree_root, rel))

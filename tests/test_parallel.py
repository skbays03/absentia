"""Tests for src/absentia/parallel.py."""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

from absentia.parallel import (
    default_jobs,
    is_free_threaded,
    mining_worker_cap,
    parse_one,
    should_parallelize,
)


def test_default_jobs_floors_to_half():
    """default_jobs returns half of detected cores, minimum 1."""
    with patch.object(os, "process_cpu_count", return_value=8):
        assert default_jobs() == 4


def test_default_jobs_minimum_one():
    """Even on a 1-core machine we use 1 worker, not 0."""
    with patch.object(os, "process_cpu_count", return_value=1):
        assert default_jobs() == 1


def test_default_jobs_handles_none():
    """When os returns None (exotic env), fall back to 1."""
    with patch.object(os, "process_cpu_count", return_value=None):
        assert default_jobs() == 1


def test_should_parallelize_below_threshold():
    """One file, four workers: not worth the spawn cost."""
    assert should_parallelize(num_changed_files=1, jobs=4) is False


def test_should_parallelize_above_threshold():
    """Sixteen files, four workers (4 each): pool is justified."""
    assert should_parallelize(num_changed_files=16, jobs=4) is True


def test_should_parallelize_jobs_one():
    """jobs=1 always means serial regardless of file count."""
    assert should_parallelize(num_changed_files=10000, jobs=1) is False


# ── is_free_threaded / mining_worker_cap ───────────────────────────────


def test_is_free_threaded_matches_sys_flags():
    """is_free_threaded reads sys.flags.gil; on a regular CPython
    build it returns False, on a no-GIL build True."""
    expected = getattr(sys.flags, "gil", 1) == 0
    assert is_free_threaded() is expected


def test_mining_worker_cap_with_gil():
    """Regular CPython caps at 4 (Amdahl's `p` plateau under GIL)."""
    with patch("absentia.parallel.is_free_threaded", return_value=False):
        assert mining_worker_cap(jobs=1) == 1
        assert mining_worker_cap(jobs=4) == 4
        assert mining_worker_cap(jobs=16) == 4  # capped


def test_mining_worker_cap_free_threaded():
    """No-GIL build caps at 7 (one per mining strategy)."""
    with patch("absentia.parallel.is_free_threaded", return_value=True):
        assert mining_worker_cap(jobs=1) == 1
        assert mining_worker_cap(jobs=4) == 4
        assert mining_worker_cap(jobs=7) == 7
        assert mining_worker_cap(jobs=16) == 7  # capped


def test_mining_worker_cap_minimum_one():
    """Even with jobs=0, never less than 1 worker."""
    with patch("absentia.parallel.is_free_threaded", return_value=False):
        assert mining_worker_cap(jobs=0) == 1
    with patch("absentia.parallel.is_free_threaded", return_value=True):
        assert mining_worker_cap(jobs=0) == 1


def test_parse_one_python():
    """Worker entry point: runs parse + extract on one file in-process."""
    src = b"def foo():\n    pass\n\ndef bar():\n    foo()\n"
    rel, items = parse_one(("example.py", src, "python"))
    assert rel == "example.py"
    assert len(items) == 2
    names = sorted(e.qualified_name for e, _ in items)
    assert names == ["example.py::bar", "example.py::foo"]

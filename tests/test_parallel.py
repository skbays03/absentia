"""Tests for src/lacuna/parallel.py."""
from __future__ import annotations

import os
from unittest.mock import patch

from lacuna.parallel import default_jobs, parse_one, should_parallelize


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


def test_parse_one_python():
    """Worker entry point: runs parse + extract on one file in-process."""
    src = b"def foo():\n    pass\n\ndef bar():\n    foo()\n"
    rel, items = parse_one(("example.py", src, "python"))
    assert rel == "example.py"
    assert len(items) == 2
    names = sorted(e.qualified_name for e, _ in items)
    assert names == ["example.py::bar", "example.py::foo"]

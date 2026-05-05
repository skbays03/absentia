"""Tests for src/lacuna/estimator.py."""
from __future__ import annotations

from lacuna.estimator import (
    DEFAULT_BPS,
    M_SERIES_BPS,
    PARALLEL_FRACTION,
    WORKER_STARTUP_S,
    amdahl_speedup,
    estimate,
    format_estimate_report,
    jobs_curve,
    serial_time_for,
    walk_corpus,
)
from lacuna.estimator import CorpusShape


def test_amdahl_jobs_one_is_unity():
    """N=1 worker means no parallelism, speedup is 1.0 by definition."""
    for p in (0.0, 0.5, 0.8, 1.0):
        assert amdahl_speedup(p, 1) == 1.0


def test_amdahl_monotonic_in_jobs():
    """More workers never decrease speedup (until cores run out)."""
    p = 0.8
    last = 0.0
    for n in (1, 2, 4, 8, 16, 32, 64):
        s = amdahl_speedup(p, n)
        assert s >= last
        last = s


def test_amdahl_asymptote():
    """At ``n → ∞`` speedup approaches 1/(1-p). With p=0.8, asymptote=5x."""
    s = amdahl_speedup(0.8, 10_000)
    assert 4.99 < s <= 5.0


def test_amdahl_p_zero_no_parallelism():
    """Fully serial workload sees no speedup at any N."""
    for n in (2, 4, 8, 100):
        assert amdahl_speedup(0.0, n) == 1.0


def test_serial_time_uses_per_language_throughput():
    """Two languages with different BPS sum independently."""
    bytes_by_lang = {"python": 32_000_000, "c": 10_000_000}
    # python: 32MB / 32MB/s = 1s; c: 10MB / 10MB/s = 1s; total = 2s
    t = serial_time_for(bytes_by_lang)
    assert 1.95 < t < 2.05


def test_serial_time_unknown_language_uses_default():
    """Languages not in the BPS table fall through to DEFAULT_BPS."""
    t = serial_time_for({"esoterica": DEFAULT_BPS})
    assert 0.95 < t < 1.05


def test_estimate_includes_worker_startup_overhead():
    """Larger N adds back per-worker spawn cost — when work is big enough."""
    # 100s of work: parallel mode is clearly worth it, no clamp engages.
    bytes_by_lang = {"python": M_SERIES_BPS["python"] * 100}
    e1 = estimate(by_language_bytes=bytes_by_lang, jobs=1)
    e8 = estimate(by_language_bytes=bytes_by_lang, jobs=8)
    expected_overhead = 7 * WORKER_STARTUP_S
    pure_parallel = e1.serial_time_s / amdahl_speedup(PARALLEL_FRACTION, 8)
    assert abs(e8.parallel_time_s - pure_parallel - expected_overhead) < 0.001


def test_estimate_clamps_to_serial_on_tiny_corpus():
    """Real lacuna won't spawn workers when overhead exceeds work — the
    estimator must match: parallel time can never exceed serial time.
    """
    # 0.01s of actual work; jobs=10 would naively add 1.35s of overhead.
    bytes_by_lang = {"python": M_SERIES_BPS["python"] // 100}
    e10 = estimate(by_language_bytes=bytes_by_lang, jobs=10)
    assert e10.parallel_time_s <= e10.serial_time_s
    assert e10.speedup == 1.0  # no actual speedup possible


def test_estimate_efficiency_is_speedup_over_n():
    """Parallel efficiency tapers as N grows."""
    bytes_by_lang = {"python": 100_000_000}  # ~3s of work
    e1 = estimate(by_language_bytes=bytes_by_lang, jobs=1)
    e8 = estimate(by_language_bytes=bytes_by_lang, jobs=8)
    e16 = estimate(by_language_bytes=bytes_by_lang, jobs=16)
    assert e1.efficiency == 1.0
    assert e8.efficiency < 1.0
    assert e16.efficiency < e8.efficiency


def test_jobs_curve_includes_powers_of_two():
    """The curve hits 1, 2, 4, 8, 16 on a 16-core machine."""
    points = jobs_curve({"python": 1_000_000_000}, max_jobs=16)
    job_counts = [e.jobs for e in points]
    assert job_counts == [1, 2, 4, 8, 16]


def test_jobs_curve_includes_non_power_max():
    """A 12-core machine gets 1, 2, 4, 8, 12."""
    points = jobs_curve({"python": 1_000_000_000}, max_jobs=12)
    assert [e.jobs for e in points] == [1, 2, 4, 8, 12]


def test_jobs_curve_minimum():
    """A 1-core machine has only one row."""
    points = jobs_curve({"python": 1_000}, max_jobs=1)
    assert len(points) == 1
    assert points[0].jobs == 1


def test_walk_corpus_tallies_per_language(tmp_path):
    """Walk a real disk tree, confirm bytes-per-language is accurate."""
    (tmp_path / "a.py").write_text("def foo():\n    pass\n")
    (tmp_path / "b.py").write_text("def bar():\n    pass\n")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.js").write_text("function baz() { return 1; }\n")

    from lacuna.extractors import discover_extractors, extension_dispatch
    extractors = discover_extractors(["python", "javascript"])
    ext_to = extension_dispatch(extractors)
    shape = walk_corpus(tmp_path, ext_to)

    assert shape.files == 3
    assert shape.by_language_files == {"python": 2, "javascript": 1}
    assert shape.by_language_bytes["python"] > 0
    assert shape.by_language_bytes["javascript"] > 0
    assert shape.bytes == sum(shape.by_language_bytes.values())


def test_walk_corpus_skips_unknown_extensions(tmp_path):
    """Files outside the configured language set are ignored."""
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "ignore.txt").write_text("not source\n")
    (tmp_path / "data.json").write_text("{}\n")

    from lacuna.extractors import discover_extractors, extension_dispatch
    extractors = discover_extractors(["python"])
    ext_to = extension_dispatch(extractors)
    shape = walk_corpus(tmp_path, ext_to)

    assert shape.files == 1
    assert list(shape.by_language_bytes.keys()) == ["python"]


def test_format_report_marks_default_jobs(tmp_path):
    """The default jobs row gets a visible marker."""
    shape = CorpusShape(
        files=10,
        bytes=1_000_000,
        by_language_bytes={"python": 1_000_000},
        by_language_files={"python": 10},
    )
    report = format_estimate_report(
        root=tmp_path,
        shape=shape,
        cpu_count=8,
        default_jobs=4,
        calibrated=False,
    )
    assert "← default" in report
    assert "uncalibrated" in report
    assert "1,000,000" not in report  # bytes are formatted, not raw


def test_format_report_calibrated_label(tmp_path):
    """Calibrated runs say so + show timestamp."""
    shape = CorpusShape(
        files=5,
        bytes=500_000,
        by_language_bytes={"python": 500_000},
        by_language_files={"python": 5},
    )
    report = format_estimate_report(
        root=tmp_path,
        shape=shape,
        cpu_count=4,
        default_jobs=2,
        calibrated=True,
        calibrated_at="2026-05-05T12:00:00Z",
    )
    assert "calibrated on this machine" in report
    assert "2026-05-05" in report
    assert "uncalibrated" not in report


def test_format_report_surfaces_observed_scan(tmp_path):
    """When a prior cold scan time exists, surface it as ground truth."""
    shape = CorpusShape(
        files=10,
        bytes=1_000_000,
        by_language_bytes={"python": 1_000_000},
        by_language_files={"python": 10},
    )
    report = format_estimate_report(
        root=tmp_path,
        shape=shape,
        cpu_count=4,
        default_jobs=2,
        calibrated=False,
        observed_cold_scan_s=0.85,
    )
    assert "Last actual cold scan" in report
    assert "ground truth" in report
    # 0.85 → "0.8 s" or "0.9 s" depending on banker's rounding; either is fine.
    assert ("0.8 s" in report) or ("0.9 s" in report)


def test_format_report_too_small_note(tmp_path):
    """Tiny corpus shows the 'parallelism won't help' explainer."""
    shape = CorpusShape(
        files=5,
        bytes=10_000,  # 10 KB Python = effectively 0s of work
        by_language_bytes={"python": 10_000},
        by_language_files={"python": 5},
    )
    report = format_estimate_report(
        root=tmp_path,
        shape=shape,
        cpu_count=8,
        default_jobs=4,
        calibrated=False,
    )
    assert "too small for parallelism" in report
    assert "Worker spawn cost" in report


def test_format_report_no_too_small_note_on_big_corpus(tmp_path):
    """A corpus large enough for real parallelism: skip the explainer."""
    shape = CorpusShape(
        files=10000,
        bytes=500_000_000,  # 500 MB Python = ~16s of work
        by_language_bytes={"python": 500_000_000},
        by_language_files={"python": 10000},
    )
    report = format_estimate_report(
        root=tmp_path,
        shape=shape,
        cpu_count=8,
        default_jobs=4,
        calibrated=False,
    )
    assert "too small for parallelism" not in report

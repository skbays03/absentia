"""Tests for src/lacuna/calibration.py."""
from __future__ import annotations

import json

import pytest

from lacuna.calibration import (
    CALIBRATION_FILENAME,
    CalibrationData,
    calibrated_bps_table,
    calibration_path,
    is_stale,
    load_calibration,
    save_calibration,
)


def _sample_data(**overrides) -> CalibrationData:
    base = {
        "calibrated_at": "2026-05-05T12:00:00+00:00",
        "lacuna_version": "0.1.0",
        "core_count": 8,
        "machine_speed_factor": 1.04,
        "calibration_corpus_path": "/tmp/example",
        "calibration_files": 200,
        "calibration_bytes": 5_000_000,
        "calibration_duration_s": 1.4,
    }
    base.update(overrides)
    return CalibrationData(**base)


def test_calibration_path_default():
    p = calibration_path()
    assert p.name == CALIBRATION_FILENAME
    assert p.parent.name == ".lacuna"


def test_load_returns_none_when_missing(tmp_path):
    p = tmp_path / "calibration.json"
    assert load_calibration(p) is None


def test_save_load_roundtrip(tmp_path):
    p = tmp_path / "calibration.json"
    original = _sample_data()
    save_calibration(original, p)
    loaded = load_calibration(p)
    assert loaded == original


def test_save_writes_atomically(tmp_path):
    """The .tmp file must not linger after a successful write."""
    p = tmp_path / "calibration.json"
    save_calibration(_sample_data(), p)
    assert p.exists()
    # No leftover tmp file
    assert not (p.with_suffix(".json.tmp")).exists()


def test_load_corrupt_returns_none(tmp_path):
    p = tmp_path / "calibration.json"
    p.write_text("not json {")
    assert load_calibration(p) is None


def test_load_missing_field_returns_none(tmp_path):
    """A JSON file lacking required fields is treated as no-calibration."""
    p = tmp_path / "calibration.json"
    p.write_text(json.dumps({"calibrated_at": "x"}))
    assert load_calibration(p) is None


def test_is_stale_version_change():
    data = _sample_data(lacuna_version="0.0.5")
    stale, reason = is_stale(data, current_version="0.1.0", current_cores=8)
    assert stale is True
    assert "lacuna upgraded" in reason


def test_is_stale_core_count_change():
    data = _sample_data(core_count=4)
    stale, reason = is_stale(data, current_version="0.1.0", current_cores=8)
    assert stale is True
    assert "core count" in reason


def test_is_stale_no_change():
    data = _sample_data()
    stale, reason = is_stale(data, current_version="0.1.0", current_cores=8)
    assert stale is False
    assert reason is None


def test_is_stale_age_threshold():
    """A calibration older than max_age_days is flagged stale."""
    from datetime import datetime, timedelta, timezone

    from lacuna.calibration import CALIBRATION_MAX_AGE_DAYS

    old_ts = (
        datetime.now(timezone.utc) - timedelta(days=CALIBRATION_MAX_AGE_DAYS + 1)
    ).isoformat()
    data = _sample_data(calibrated_at=old_ts)
    stale, reason = is_stale(data, current_version="0.1.0", current_cores=8)
    assert stale is True
    assert "days old" in reason


def test_is_stale_recent_calibration_not_aged():
    """A recent calibration (under threshold) is not flagged for age."""
    from datetime import datetime, timedelta, timezone

    recent_ts = (
        datetime.now(timezone.utc) - timedelta(days=10)
    ).isoformat()
    data = _sample_data(calibrated_at=recent_ts)
    stale, reason = is_stale(data, current_version="0.1.0", current_cores=8)
    assert stale is False


def test_is_stale_unparseable_timestamp_does_not_crash():
    """A garbage calibrated_at field shouldn't break is_stale."""
    data = _sample_data(calibrated_at="not a date")
    stale, reason = is_stale(data, current_version="0.1.0", current_cores=8)
    assert stale is False  # unparseable → fall through, don't refuse


def test_calibrated_bps_table_scales_baseline():
    """factor < 1 produces slower BPS; factor > 1 produces faster."""
    from lacuna.estimator import M_SERIES_BPS

    slow = calibrated_bps_table(0.5)
    fast = calibrated_bps_table(2.0)

    for lang, baseline in M_SERIES_BPS.items():
        assert slow[lang] < baseline
        assert fast[lang] > baseline
        # Roughly correct (allow ±1 for int truncation)
        assert abs(slow[lang] - baseline * 0.5) <= 1
        assert abs(fast[lang] - baseline * 2.0) <= 1


def test_calibrated_bps_table_minimum_one():
    """Pathologically tiny factor still yields a valid BPS (>0)."""
    table = calibrated_bps_table(0.000001)
    for bps in table.values():
        assert bps >= 1


def test_run_calibration_rejects_too_few_files(tmp_path):
    """A corpus below the file-count threshold should error cleanly."""
    from lacuna.calibration import run_calibration
    from lacuna.config import Config

    (tmp_path / "x.py").write_text("def f(): pass\n")
    with pytest.raises(ValueError, match="at least"):
        run_calibration(corpus_root=tmp_path, config=Config())


def test_fit_amdahl_p_recovers_known_value():
    """Synthesize observations from a known p, confirm we recover it."""
    from lacuna.calibration import fit_amdahl_p
    from lacuna.estimator import amdahl_speedup

    true_p = 0.85
    baseline = 10.0
    obs = [(1, baseline)]
    for n in (2, 4, 8, 16):
        obs.append((n, baseline / amdahl_speedup(true_p, n)))
    fitted = fit_amdahl_p(obs)
    # Grid step is 0.01 so allow a small fit window.
    assert abs(fitted - true_p) <= 0.02


def test_fit_amdahl_p_falls_back_without_baseline():
    """No jobs=1 point → return PARALLEL_FRACTION default."""
    from lacuna.calibration import fit_amdahl_p
    from lacuna.estimator import PARALLEL_FRACTION

    obs = [(2, 5.0), (4, 3.0)]
    assert fit_amdahl_p(obs) == PARALLEL_FRACTION


def test_fit_amdahl_p_falls_back_with_only_baseline():
    """Only jobs=1 point → can't compute speedups → fall back."""
    from lacuna.calibration import fit_amdahl_p
    from lacuna.estimator import PARALLEL_FRACTION

    assert fit_amdahl_p([(1, 5.0)]) == PARALLEL_FRACTION


def test_select_amdahl_points_capped_at_four():
    """Even on a 64-core machine we measure at most 4 jobs counts."""
    from lacuna.calibration import _select_amdahl_points

    points = _select_amdahl_points(64)
    assert len(points) == 4
    assert points[0] == 1
    assert all(p <= 64 for p in points)


def test_select_amdahl_points_includes_all_low_cores():
    """1, 2, 4, 8 covers an 8-core machine entirely."""
    from lacuna.calibration import _select_amdahl_points

    assert _select_amdahl_points(8) == [1, 2, 4, 8]


def test_make_synthetic_corpus_meets_minimums(tmp_path):
    """The bundled synthetic corpus should always satisfy calibration thresholds."""
    from lacuna.calibration import (
        MIN_CALIBRATION_BYTES,
        MIN_CALIBRATION_FILES,
        make_synthetic_corpus,
    )

    corpus = make_synthetic_corpus(tmp_path / "synth")
    files = list(corpus.glob("*.py"))
    total_bytes = sum(f.stat().st_size for f in files)

    assert len(files) >= MIN_CALIBRATION_FILES
    assert total_bytes >= MIN_CALIBRATION_BYTES


def test_make_synthetic_corpus_is_idempotent(tmp_path):
    """Calling twice into the same dir doesn't blow up or duplicate."""
    from lacuna.calibration import make_synthetic_corpus

    target = tmp_path / "synth"
    make_synthetic_corpus(target)
    count1 = len(list(target.glob("*.py")))
    make_synthetic_corpus(target)
    count2 = len(list(target.glob("*.py")))
    assert count1 == count2  # same files, overwritten not appended


def test_synthetic_corpus_extracts_real_entities(tmp_path):
    """Sanity check: the synthetic files actually parse and yield entities."""
    from lacuna.calibration import make_synthetic_corpus
    from lacuna.estimator import walk_corpus
    from lacuna.extractors import discover_extractors, extension_dispatch

    corpus = make_synthetic_corpus(tmp_path / "synth", num_files=10)
    extractors = discover_extractors(["python"])
    ext_to = extension_dispatch(extractors)
    shape = walk_corpus(corpus, ext_to)
    assert shape.files == 10
    assert shape.bytes > 0


def test_run_calibration_succeeds_on_sufficient_corpus(tmp_path):
    """End-to-end: scan a synthetic corpus, derive a speed factor."""
    from lacuna.calibration import (
        MIN_CALIBRATION_FILES,
        run_calibration,
    )
    from lacuna.config import Config

    # Generate a corpus that meets BOTH minimums (files and bytes).
    # ~4 KB per file × 35 files = ~140 KB total, comfortably above
    # the 100 KB byte threshold.
    padding = "# " + ("x" * 4000) + "\n"
    for i in range(MIN_CALIBRATION_FILES + 5):
        (tmp_path / f"f_{i}.py").write_text(
            "def alpha(x):\n"
            "    return x + 1\n"
            "\n"
            "def beta(y):\n"
            "    return alpha(y) * 2\n"
            f"{padding}"
        )

    data = run_calibration(corpus_root=tmp_path, config=Config())
    assert data.calibration_files >= MIN_CALIBRATION_FILES
    assert data.calibration_duration_s > 0
    assert data.machine_speed_factor > 0
    assert data.lacuna_version
    assert data.core_count >= 1

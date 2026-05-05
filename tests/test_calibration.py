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

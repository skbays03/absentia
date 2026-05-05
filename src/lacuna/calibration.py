"""First-run machine-speed calibration.

Phase 2 of the estimator. Scans a small user-chosen corpus at
``--jobs 1`` to measure actual single-process throughput on the
user's hardware, then derives a ``machine_speed_factor`` that
scales the placeholder M-series baseline BPS table to match
reality.

Cache: ``~/.lacuna/calibration.json``. Per-machine, per-user — not
per-project; the same hardware-throughput result applies to every
project the user scans.

Stale-detection re-prompts (or silently re-runs) when:

  - the cache file is missing (first run);
  - lacuna's version changed since calibration (extractors may have
    shifted; throughput baseline drifts);
  - core count changed (laptop swap, container CPU limits changed);
  - the user passes ``--recalibrate``.

Atomic writes: ``calibration.json.tmp`` → rename, so a Ctrl+C
mid-write leaves no partial file behind.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__


CALIBRATION_FILENAME = "calibration.json"

# Minimum corpus size for reliable calibration. Below this, the
# timing signal is dominated by noise (fixed pipeline overhead +
# OS scheduling jitter) rather than actual scanning throughput.
MIN_CALIBRATION_FILES = 30
MIN_CALIBRATION_BYTES = 100_000  # 100 KB


@dataclass(frozen=True)
class CalibrationData:
    """Cache record. JSON-serializable."""
    calibrated_at: str           # ISO 8601 UTC
    lacuna_version: str
    core_count: int
    machine_speed_factor: float  # multiplied onto baseline BPS
    calibration_corpus_path: str
    calibration_files: int
    calibration_bytes: int
    calibration_duration_s: float
    amdahl_p: float = 0.80       # phase 3 may fit this from a curve


def calibration_path() -> Path:
    """Default cache location: ``~/.lacuna/calibration.json``."""
    return Path.home() / ".lacuna" / CALIBRATION_FILENAME


def load_calibration(path: Path | None = None) -> CalibrationData | None:
    """Read the cache; return ``None`` if missing or unparseable.

    Unparseable means the file exists but JSON is invalid or fields
    are missing — equivalent to "no calibration" for caller purposes.
    """
    p = path if path is not None else calibration_path()
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text())
        return CalibrationData(**raw)
    except (OSError, ValueError, TypeError):
        return None


def save_calibration(
    data: CalibrationData, path: Path | None = None,
) -> None:
    """Write atomically: tmp + rename."""
    p = path if path is not None else calibration_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(asdict(data), indent=2) + "\n")
    tmp.replace(p)


def is_stale(
    data: CalibrationData,
    *,
    current_version: str | None = None,
    current_cores: int | None = None,
) -> tuple[bool, str | None]:
    """Return ``(is_stale, human_reason)``.

    ``current_version`` and ``current_cores`` exist for testing; in
    production they default to the live values.
    """
    cv = current_version if current_version is not None else __version__
    if data.lacuna_version != cv:
        return True, (
            f"lacuna upgraded since calibration "
            f"({data.lacuna_version} → {cv})"
        )
    cc = current_cores if current_cores is not None else detect_cores()
    if data.core_count != cc:
        return True, (
            f"core count changed since calibration "
            f"({data.core_count} → {cc})"
        )
    return False, None


def detect_cores() -> int:
    """Cores reported by the OS — same logic as ``parallel.default_jobs``."""
    n: int | None
    if hasattr(os, "process_cpu_count"):
        n = os.process_cpu_count()
    else:
        n = os.cpu_count()
    return n if n and n > 0 else 1


def run_calibration(
    *,
    corpus_root: Path,
    config: Any,
) -> CalibrationData:
    """Scan ``corpus_root`` at jobs=1, derive a machine_speed_factor.

    Uses a temporary state directory so the user's ``.lacuna/`` cache
    isn't polluted by calibration runs (and so we measure a true cold
    scan, not a warm one).

    Raises ``ValueError`` if the corpus is too small to calibrate
    reliably.
    """
    from .estimator import serial_time_for, walk_corpus
    from .extractors import discover_extractors, extension_dispatch
    from .storage import SCHEMA_VERSION

    extractors = discover_extractors(config.scan.languages)
    if not extractors:
        raise ValueError(
            "no extractors available for the configured languages"
        )
    ext_to_extractor = extension_dispatch(extractors)
    shape = walk_corpus(corpus_root, ext_to_extractor)

    if shape.files < MIN_CALIBRATION_FILES:
        raise ValueError(
            f"corpus has only {shape.files} source files; need at "
            f"least {MIN_CALIBRATION_FILES} for reliable calibration"
        )
    if shape.bytes < MIN_CALIBRATION_BYTES:
        raise ValueError(
            f"corpus has only {shape.bytes} bytes; need at least "
            f"{MIN_CALIBRATION_BYTES} for reliable calibration"
        )

    # Disposable state dir → guaranteed cold scan, no user-cache write.
    with tempfile.TemporaryDirectory(prefix="lacuna-cal-") as tmpd:
        tmp_state = Path(tmpd) / "state"
        tmp_state.mkdir()
        (tmp_state / "version").write_text(f"{SCHEMA_VERSION}\n")

        # Late import to avoid an import cycle at module load.
        from .cli import scan_corpus

        started = time.perf_counter()
        scan_corpus(
            root=corpus_root,
            state_dir=tmp_state,
            config=config,
            jobs=1,
            extractors=extractors,
        )
        elapsed = time.perf_counter() - started

    predicted = serial_time_for(shape.by_language_bytes)
    if predicted <= 0 or elapsed <= 0:
        factor = 1.0
    else:
        factor = predicted / elapsed

    return CalibrationData(
        calibrated_at=datetime.now(timezone.utc).isoformat(),
        lacuna_version=__version__,
        core_count=detect_cores(),
        machine_speed_factor=factor,
        calibration_corpus_path=str(corpus_root),
        calibration_files=shape.files,
        calibration_bytes=shape.bytes,
        calibration_duration_s=elapsed,
    )


def calibrated_bps_table(machine_speed_factor: float) -> dict[str, int]:
    """Apply the speed factor to the baseline BPS table.

    factor < 1 = machine slower than M-series baseline
    factor > 1 = machine faster
    """
    from .estimator import M_SERIES_BPS
    return {
        lang: max(1, int(bps * machine_speed_factor))
        for lang, bps in M_SERIES_BPS.items()
    }

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
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from . import __version__


CALIBRATION_FILENAME = "calibration.json"

# Minimum corpus size for reliable calibration. Below this, the
# timing signal is dominated by noise (fixed pipeline overhead +
# OS scheduling jitter) rather than actual scanning throughput.
MIN_CALIBRATION_FILES = 30
MIN_CALIBRATION_BYTES = 100_000  # 100 KB

# Re-prompt the user to recalibrate after this many days. Catches
# drift from OS upgrades, thermal degradation, dying SSDs, and
# similar slow-moving changes that don't trigger the version- or
# core-count-based invalidation.
CALIBRATION_MAX_AGE_DAYS = 90

# Languages with fewer bytes than this in the calibration corpus
# get the global machine_speed_factor applied to their baseline,
# rather than a separate measurement. Below this threshold, fixed
# pipeline overhead (group + mine + storage, ~200ms) dominates the
# measured time and the resulting BPS is more noise than signal.
# Empirically: 138 KB of Bash measured 567 KB/s vs an actual ~3 MB/s
# at this corpus size — the overhead made it look 5× slower than reality.
MIN_BYTES_PER_LANGUAGE = 500_000  # 500 KB


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
    amdahl_p: float = 0.80
    # Optional: per-jobs measurements that produced the fit, for the
    # curious user (and as evidence in the methodology doc). Older
    # calibration files lack this; treat absence as "no observations."
    jobs_curve_observed: list[tuple[int, float]] = field(default_factory=list)
    # Optional: bytes/sec per language, measured independently during
    # calibration on languages with sufficient byte share. When a
    # language is in this dict, its value overrides
    # ``M_SERIES_BPS[lang] × machine_speed_factor``. Other languages
    # fall back to the scaled baseline.
    per_language_bps: dict[str, int] = field(default_factory=dict)


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
    now: datetime | None = None,
    max_age_days: int = CALIBRATION_MAX_AGE_DAYS,
) -> tuple[bool, str | None]:
    """Return ``(is_stale, human_reason)``.

    ``current_version``, ``current_cores``, and ``now`` exist for
    testing; in production they default to the live values.
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
    age_days = _age_in_days(data.calibrated_at, now=now)
    if age_days is not None and age_days >= max_age_days:
        return True, (
            f"calibration is {age_days} days old (≥ {max_age_days})"
        )
    return False, None


def _age_in_days(
    calibrated_at_iso: str, now: datetime | None = None,
) -> int | None:
    """Days between ``calibrated_at`` and ``now``. Returns None if
    the timestamp is unparseable (forward-compat: don't refuse to
    estimate just because we can't parse the date).
    """
    try:
        ts = datetime.fromisoformat(calibrated_at_iso)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    current = now if now is not None else datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    delta = current - ts
    return max(0, delta.days)


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
    fit_amdahl: bool = True,
    progress: Any = None,
) -> CalibrationData:
    """Scan ``corpus_root`` at jobs=1, derive a ``machine_speed_factor``,
    and (optionally) fit Amdahl's ``p`` from a multi-jobs curve.

    Uses a temporary state directory so the user's ``.lacuna/`` cache
    isn't polluted by calibration runs (every scan is therefore cold).

    When ``fit_amdahl`` is True (the default), additional scans run at
    jobs ∈ {2, 4, 8, ...} up to a bounded number of points so we can
    fit ``p`` from the observed speedup curve. Set False to skip and
    fall back to ``PARALLEL_FRACTION = 0.80``.

    ``progress`` (optional) is a callable accepting ``(jobs, n_runs)``
    invoked before each sub-scan so callers can render progress.

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

    cores = detect_cores()
    jobs_to_measure = (
        _select_amdahl_points(cores) if fit_amdahl and cores >= 2 else [1]
    )

    # Eligible per-language scans we'll also run later. Pre-compute
    # so the StepIndicator's total step count is accurate.
    eligible_langs = sorted(
        (
            lang for lang, n in shape.by_language_bytes.items()
            if n >= MIN_BYTES_PER_LANGUAGE
        ),
        key=lambda lang: -shape.by_language_bytes[lang],
    )
    total_steps = len(jobs_to_measure) + len(eligible_langs)
    indicator = (
        progress if progress is not None
        else _make_step_indicator(total_steps)
    )

    observations: list[tuple[int, float]] = []
    primary_elapsed = 0.0
    for n_jobs in jobs_to_measure:
        _step(indicator, f"scanning at jobs={n_jobs}")

        with tempfile.TemporaryDirectory(prefix="lacuna-cal-") as tmpd:
            tmp_state = Path(tmpd) / "state"
            tmp_state.mkdir()
            (tmp_state / "version").write_text(f"{SCHEMA_VERSION}\n")

            from .cli import scan_corpus  # late import: avoid cycle
            from .progress import ticking as _ticking

            # Bridge: scan_corpus's per-file progress callback feeds
            # the StepIndicator's sub-line so the user sees files
            # flash by during each calibration sub-scan.
            cb = _make_indicator_progress_bridge(indicator)

            started = time.perf_counter()
            with _ticker_if_indicator(indicator, _ticking):
                scan_corpus(
                    root=corpus_root,
                    state_dir=tmp_state,
                    config=config,
                    jobs=n_jobs,
                    extractors=extractors,
                    progress_callback=cb,
                )
            elapsed = time.perf_counter() - started
        observations.append((n_jobs, elapsed))
        if n_jobs == 1:
            primary_elapsed = elapsed

    predicted = serial_time_for(shape.by_language_bytes)
    factor = (
        predicted / primary_elapsed
        if predicted > 0 and primary_elapsed > 0 else 1.0
    )
    fitted_p = (
        fit_amdahl_p(observations)
        if fit_amdahl and len(observations) >= 2 else 0.80
    )

    per_lang_bps = calibrate_per_language(
        corpus_root=corpus_root,
        config=config,
        shape=shape,
        progress=indicator,
    )

    # Finish the indicator we created (caller-provided ones stay open).
    if progress is None and indicator is not None:
        try:
            indicator.finish()
        except Exception:
            pass

    return CalibrationData(
        calibrated_at=datetime.now(timezone.utc).isoformat(),
        lacuna_version=__version__,
        core_count=cores,
        machine_speed_factor=factor,
        calibration_corpus_path=str(corpus_root),
        calibration_files=shape.files,
        calibration_bytes=shape.bytes,
        calibration_duration_s=primary_elapsed,
        amdahl_p=fitted_p,
        jobs_curve_observed=observations,
        per_language_bps=per_lang_bps,
    )


def calibrate_per_language(
    *,
    corpus_root: Path,
    config: Any,
    shape: Any = None,
    min_bytes: int = MIN_BYTES_PER_LANGUAGE,
    progress: Any = None,
) -> dict[str, int]:
    """Measure bytes/sec independently for each language with enough
    byte share in the corpus. Returns ``{language_name: bps}``.

    Each eligible language gets its own jobs=1 cold scan with the
    config narrowed to that language only. Languages below
    ``min_bytes`` are skipped — their timing signal is too noisy
    to fit (fixed pipeline overhead dominates per-byte cost).

    Pass ``shape`` to avoid re-walking the corpus; otherwise we
    walk it ourselves.
    """
    from dataclasses import replace
    from .estimator import walk_corpus
    from .extractors import discover_extractors, extension_dispatch
    from .storage import SCHEMA_VERSION

    if shape is None:
        extractors = discover_extractors(config.scan.languages)
        ext_to = extension_dispatch(extractors)
        shape = walk_corpus(corpus_root, ext_to)

    eligible = sorted(
        (lang for lang, n in shape.by_language_bytes.items() if n >= min_bytes),
        key=lambda lang: -shape.by_language_bytes[lang],
    )
    if not eligible:
        return {}

    per_lang_bps: dict[str, int] = {}
    for lang in eligible:
        _step(progress, f"per-language scan: {lang}")

        sub_config = replace(
            config,
            scan=replace(config.scan, languages=[lang]),
        )
        sub_extractors = discover_extractors(sub_config.scan.languages)
        if not sub_extractors:
            continue

        with tempfile.TemporaryDirectory(prefix="lacuna-cal-lang-") as tmpd:
            tmp_state = Path(tmpd) / "state"
            tmp_state.mkdir()
            (tmp_state / "version").write_text(f"{SCHEMA_VERSION}\n")

            from .cli import scan_corpus  # late import: avoid cycle
            from .progress import ticking as _ticking

            cb = _make_indicator_progress_bridge(progress)

            started = time.perf_counter()
            with _ticker_if_indicator(progress, _ticking):
                scan_corpus(
                    root=corpus_root,
                    state_dir=tmp_state,
                    config=sub_config,
                    jobs=1,
                    extractors=sub_extractors,
                    progress_callback=cb,
                )
            elapsed = time.perf_counter() - started

        if elapsed > 0:
            per_lang_bps[lang] = max(
                1, int(shape.by_language_bytes[lang] / elapsed)
            )

    return per_lang_bps


def _make_step_indicator(total_steps: int) -> Any:
    """Build the default StepIndicator for calibration progress."""
    from .progress import StepIndicator
    return StepIndicator(total_steps=total_steps, prefix="[calibrating]")


def _step(indicator: Any, label: str) -> None:
    """Best-effort step transition; never break calibration on a UI bug."""
    if indicator is None:
        return
    try:
        indicator.step(label)
    except Exception:
        pass


def _make_indicator_progress_bridge(indicator: Any) -> Any:
    """Return a ``progress_callback(n, item=...)``-compatible function
    that updates ``indicator``'s current-item sub-line.

    Returns None when ``indicator`` is None or has no
    ``set_current_item`` method (defensive for non-progress callers).
    Calibration's StepIndicator is the typical target — the bridge
    lets each sub-scan's per-file ticks flow into the step's sub-
    display so the user sees what calibration is currently looking at.
    """
    if indicator is None or not hasattr(indicator, "set_current_item"):
        return None

    def bridge(n: int = 1, item: str | None = None) -> None:
        if item is None:
            return
        try:
            indicator.set_current_item(item)
        except Exception:
            pass  # UI bridge must never break the scan

    return bridge


@contextmanager
def _ticker_if_indicator(indicator: Any, ticking_fn: Any) -> Iterator[None]:
    """Wrap ``ticking_fn(indicator)`` only when ``indicator`` looks
    tick-able. Otherwise yield immediately (no-op)."""
    if indicator is None or not hasattr(indicator, "tick"):
        yield
        return
    try:
        with ticking_fn(indicator):
            yield
    except Exception:
        # If the ticker setup explodes, just run the work without it.
        yield


def _select_amdahl_points(cores: int) -> list[int]:
    """Powers of 2 up to ``cores``, plus ``cores`` if it isn't one.

    Capped at 4 measurement points so calibration time scales
    predictably (each extra point adds another full scan worth of
    user-visible cost).
    """
    points: list[int] = []
    n = 1
    while n <= cores and len(points) < 4:
        points.append(n)
        n *= 2
    if cores not in points and len(points) < 4:
        points.append(cores)
    return sorted(set(points))


def fit_amdahl_p(
    observations: list[tuple[int, float]],
    *,
    p_min: float = 0.20,
    p_max: float = 0.99,
    step: float = 0.01,
) -> float:
    """Find the Amdahl ``p`` that best explains observed scan times.

    ``observations`` is a list of ``(jobs, elapsed_seconds)`` from a
    real calibration run. Point ``jobs=1`` provides the serial
    baseline; subsequent points give observed speedups
    (``baseline / elapsed``). We grid-search ``p`` over [p_min, p_max]
    and pick the value minimizing squared residuals between
    Amdahl-predicted and observed speedups.

    Falls back to ``PARALLEL_FRACTION`` if input lacks a baseline or
    has only one point.
    """
    from .estimator import PARALLEL_FRACTION, amdahl_speedup

    baseline = next((t for n, t in observations if n == 1), None)
    if baseline is None or baseline <= 0:
        return PARALLEL_FRACTION
    measured = [(n, baseline / t) for n, t in observations if n > 1 and t > 0]
    if not measured:
        return PARALLEL_FRACTION

    best_p = PARALLEL_FRACTION
    best_err = float("inf")
    p = p_min
    while p <= p_max + 1e-9:
        err = sum(
            (amdahl_speedup(p, n) - obs_sp) ** 2 for n, obs_sp in measured
        )
        if err < best_err:
            best_err = err
            best_p = p
        p += step
    return round(best_p, 3)


def make_synthetic_corpus(
    target_dir: Path, num_files: int = 60,
) -> Path:
    """Generate a small synthetic Python corpus suitable for calibration.

    Produces ``num_files`` files (~3 KB each) with realistic-shaped
    Python: a couple of free functions, a class with methods, type
    hints, and decorator uses. Enough AST structure to exercise the
    Python extractor; bounded enough to bundle conceptually (we
    materialize at runtime — no package data needed).

    Returns ``target_dir`` for convenience.
    """
    target_dir.mkdir(parents=True, exist_ok=True)

    template = (
        '"""Synthetic file {i} for lacuna calibration."""\n'
        'from __future__ import annotations\n'
        '\n'
        'from typing import Any\n'
        '\n'
        '\n'
        'def alpha_{i}(x: int) -> int:\n'
        '    """Increment x."""\n'
        '    return x + 1\n'
        '\n'
        '\n'
        'def beta_{i}(y: int) -> int:\n'
        '    return alpha_{i}(y) * 2\n'
        '\n'
        '\n'
        'class Calc_{i}:\n'
        '    """Synthetic calculator."""\n'
        '\n'
        '    def __init__(self, base: int = 0) -> None:\n'
        '        self.base = base\n'
        '\n'
        '    def add(self, x: int) -> int:\n'
        '        return self.base + alpha_{i}(x)\n'
        '\n'
        '    def mul(self, x: int, y: int) -> int:\n'
        '        return alpha_{i}(x) * beta_{i}(y)\n'
        '\n'
        '\n'
        'def gamma_{i}() -> list[int]:\n'
        '    """Build a small list using the helpers above."""\n'
        '    return [alpha_{i}(i) for i in range(10)]\n'
        '\n'
        # Padding to push each file above ~3 KB so the corpus comfortably
        # exceeds MIN_CALIBRATION_BYTES (100 KB) at 60 files.
        + ("# " + "x" * 200 + "\n") * 12
    )

    for i in range(num_files):
        (target_dir / f"synth_{i:03d}.py").write_text(template.format(i=i))

    return target_dir


def calibrated_bps_table(
    machine_speed_factor: float,
    per_language_bps: dict[str, int] | None = None,
) -> dict[str, int]:
    """Build the BPS table from calibration outputs.

    Two-layer logic:

    1. ``machine_speed_factor`` × ``M_SERIES_BPS[lang]`` — global
       fallback for every language (factor < 1 = machine slower
       than baseline; factor > 1 = faster).

    2. ``per_language_bps[lang]`` — when present, *overrides* the
       global fallback for that language. These come from
       per-language scans during calibration; they're more accurate
       because they capture parser-specific cost without averaging
       across the whole corpus.
    """
    from .estimator import M_SERIES_BPS
    table: dict[str, int] = {}
    overrides = per_language_bps or {}
    for lang, baseline in M_SERIES_BPS.items():
        if lang in overrides:
            table[lang] = max(1, int(overrides[lang]))
        else:
            table[lang] = max(1, int(baseline * machine_speed_factor))
    return table

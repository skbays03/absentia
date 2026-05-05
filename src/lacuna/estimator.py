"""Cold-scan time estimator.

Predicts ``lacuna check`` wall-clock from the corpus shape (bytes
per language) and a parallelism level. Two pieces:

1. **Per-language throughput** — bytes/sec at jobs=1, derived from
   benchmark scans on canonical corpora. C is ~3-5× slower per byte
   than Python because of deeper ASTs and larger translation units.

2. **Amdahl's law** — speedup with N workers is
   ``1 / ((1-p) + p/N)`` where ``p`` ≈ 0.80 (parse + extract is the
   parallel ~80%; group + mine + storage is the serial ~20%). The
   asymptote is therefore 1/(1-p) ≈ 5×, no matter how many cores
   you throw at it.

The constants below are M-series MacBook baselines. They get
overwritten by ``~/.lacuna/calibration.json`` once the user runs
``lacuna est`` interactively for the first time. Until then every
estimate is labeled "uncalibrated" in the output.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


# Per-language throughput at jobs=1 on the calibration baseline machine
# (M-series MacBook). Bytes per second. Derived from the benchmark
# table in docs/explanation/architecture.md combined with rough corpus
# sizes; expect ±30% variance on the same hardware before calibration
# refines them.
M_SERIES_BPS: dict[str, int] = {
    "python":     32_000_000,
    "javascript": 18_000_000,
    "typescript": 18_000_000,
    "rust":       19_000_000,
    "go":         22_000_000,
    "java":       16_000_000,
    "ruby":       28_000_000,
    "csharp":     13_000_000,
    "swift":      15_000_000,
    "c":          10_000_000,
    "cpp":         9_000_000,
    "php":        25_000_000,
    "kotlin":     12_000_000,
    "scala":      14_000_000,
    "lua":        35_000_000,
    "bash":       40_000_000,
}

# Catch-all for languages not yet in the baseline table.
DEFAULT_BPS: int = 15_000_000

# Parallelizable fraction. Empirically ~0.80 for lacuna's pipeline.
PARALLEL_FRACTION: float = 0.80

# Per-worker startup cost: process spawn + tree-sitter grammar load.
WORKER_STARTUP_S: float = 0.15


@dataclass(frozen=True)
class Estimate:
    """Predicted cold-scan time at a specific parallelism level."""
    serial_time_s: float
    parallel_time_s: float
    jobs: int
    speedup: float
    efficiency: float
    files_by_lang_bytes: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class CorpusShape:
    """What ``lacuna est`` walks the tree to learn."""
    files: int
    bytes: int
    by_language_bytes: dict[str, int]
    by_language_files: dict[str, int]


def amdahl_speedup(p: float, n: int) -> float:
    """Speedup with ``n`` workers given parallelizable fraction ``p``.

    ``n=1`` returns 1.0 by convention. Asymptote (``n → ∞``) is
    ``1 / (1 - p)``.
    """
    if n <= 1:
        return 1.0
    return 1.0 / ((1.0 - p) + p / n)


def serial_time_for(
    by_language_bytes: dict[str, int],
    bps_table: dict[str, int] | None = None,
) -> float:
    """Predicted single-process scan time, summed across languages."""
    table = bps_table if bps_table is not None else M_SERIES_BPS
    total = 0.0
    for lang, byte_count in by_language_bytes.items():
        bps = table.get(lang, DEFAULT_BPS)
        if bps <= 0:
            continue
        total += byte_count / bps
    return total


def estimate(
    *,
    by_language_bytes: dict[str, int],
    jobs: int,
    parallel_fraction: float = PARALLEL_FRACTION,
    bps_table: dict[str, int] | None = None,
) -> Estimate:
    """Compute a single estimate at the requested parallelism level.

    Parallel time is clamped to ``≤ serial`` because lacuna has a
    serial-fallback escape hatch (``should_parallelize`` in
    ``parallel.py``). When the work itself is shorter than the
    worker-spawn overhead, real lacuna stays single-process — the
    estimator must match that behavior or it overstates cost on
    small corpora.
    """
    serial = serial_time_for(by_language_bytes, bps_table)
    sp_amdahl = amdahl_speedup(parallel_fraction, jobs)
    overhead = max(0, jobs - 1) * WORKER_STARTUP_S
    parallel = min(serial, max(0.0, serial / sp_amdahl + overhead))
    actual_speedup = (serial / parallel) if parallel > 0 else 1.0
    return Estimate(
        serial_time_s=serial,
        parallel_time_s=parallel,
        jobs=jobs,
        speedup=actual_speedup,
        efficiency=actual_speedup / max(jobs, 1),
        files_by_lang_bytes=dict(by_language_bytes),
    )


def jobs_curve(
    by_language_bytes: dict[str, int],
    max_jobs: int,
    parallel_fraction: float = PARALLEL_FRACTION,
    bps_table: dict[str, int] | None = None,
) -> list[Estimate]:
    """Estimates at jobs ∈ {1, 2, 4, 8, …} up to and including ``max_jobs``.

    Powers of two give a readable curve; we always include
    ``max_jobs`` even when it isn't a power of two (e.g. a 12-core
    machine gets 1, 2, 4, 8, 12).
    """
    if max_jobs < 1:
        max_jobs = 1
    points: list[int] = []
    n = 1
    while n <= max_jobs:
        points.append(n)
        n *= 2
    if max_jobs not in points:
        points.append(max_jobs)
    points.sort()
    return [
        estimate(
            by_language_bytes=by_language_bytes,
            jobs=j,
            parallel_fraction=parallel_fraction,
            bps_table=bps_table,
        )
        for j in points
    ]


def walk_corpus(root: Path, ext_to_extractor: dict) -> CorpusShape:
    """Tally files and bytes per language under ``root``.

    Cheap — only stats files, doesn't read content. Sub-second on the
    Linux kernel.
    """
    from .parsing import find_source_files

    by_lang_bytes: dict[str, int] = {}
    by_lang_files: dict[str, int] = {}
    total_files = 0
    total_bytes = 0

    for path in find_source_files(root, ext_to_extractor.keys()):
        extractor = ext_to_extractor.get(path.suffix.lower())
        if extractor is None:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        lang = extractor.language_name
        by_lang_bytes[lang] = by_lang_bytes.get(lang, 0) + size
        by_lang_files[lang] = by_lang_files.get(lang, 0) + 1
        total_files += 1
        total_bytes += size

    return CorpusShape(
        files=total_files,
        bytes=total_bytes,
        by_language_bytes=by_lang_bytes,
        by_language_files=by_lang_files,
    )


def cpu_count_for_estimator() -> int:
    """Cores to use as the upper bound of the estimator table.

    Prefer ``os.process_cpu_count()`` (3.13+, respects cgroups) and
    fall back to ``os.cpu_count()``. Returns 1 if neither yields a
    value.
    """
    n: int | None
    if hasattr(os, "process_cpu_count"):
        n = os.process_cpu_count()
    else:
        n = os.cpu_count()
    return n if n and n > 0 else 1


def _format_size(num_bytes: int) -> str:
    """Compact human-friendly byte size: 412 MB, 1.2 GB, 0.05 KB."""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    for unit, threshold in (("KB", 1024**2), ("MB", 1024**3), ("GB", 1024**4)):
        if num_bytes < threshold:
            return f"{num_bytes / (threshold / 1024):.1f} {unit}"
    return f"{num_bytes / (1024**4):.2f} TB"


def _format_seconds(s: float) -> str:
    """``45.2 s``, ``3 m 12 s``, ``1 h 4 m`` — readable at any scale."""
    if s < 60:
        return f"{s:.1f} s"
    if s < 3600:
        m, sec = divmod(s, 60)
        return f"{int(m)} m {int(sec):2d} s"
    h, rem = divmod(s, 3600)
    m, _ = divmod(rem, 60)
    return f"{int(h)} h {int(m)} m"


def format_estimate_report(
    *,
    root: Path,
    shape: CorpusShape,
    cpu_count: int,
    default_jobs: int,
    calibrated: bool,
    calibrated_at: str | None = None,
    observed_cold_scan_s: float | None = None,
    bps_table: dict[str, int] | None = None,
) -> str:
    """Human-readable ASCII report — the body of ``lacuna est`` output.

    The jobs-vs-time table goes up to ``cpu_count``; the user's
    default workers row is marked. A trailing footer notes whether
    the cost model is calibrated and links to the methodology doc
    for users who want to dig in.

    Pass ``bps_table`` to use a calibrated per-language throughput
    table (typically from ``calibration.calibrated_bps_table``);
    omitted means "use M-series baseline".
    """
    curve = jobs_curve(
        shape.by_language_bytes, max_jobs=cpu_count, bps_table=bps_table,
    )

    lines: list[str] = []
    lines.append(f"lacuna est — cold-scan estimate for {root}")
    lines.append("")
    lines.append(
        f"Files          {shape.files:>8,d}   ({_format_size(shape.bytes)})"
    )

    if shape.by_language_bytes:
        items = sorted(
            shape.by_language_bytes.items(), key=lambda kv: -kv[1]
        )
        lines.append("By language")
        for lang, byte_count in items:
            lines.append(
                f"               {lang:<14s} "
                f"{shape.by_language_files.get(lang, 0):>5,d} files "
                f"({_format_size(byte_count)})"
            )
        lines.append("")

    serial = curve[0].parallel_time_s
    default_est = next(
        (e for e in curve if e.jobs == default_jobs), curve[-1]
    )
    lines.append(
        f"Single-process baseline   {_format_seconds(serial)}"
    )
    lines.append(
        f"At default jobs (= {default_jobs:d})       "
        f"~{_format_seconds(default_est.parallel_time_s)}   "
        f"({default_est.speedup:.2f}× speedup, "
        f"{default_est.efficiency * 100:.0f}% efficiency)"
    )
    if observed_cold_scan_s is not None:
        lines.append(
            f"Last actual cold scan     "
            f"{_format_seconds(observed_cold_scan_s)}   "
            f"(from .lacuna/last_run.json — ground truth)"
        )
    lines.append("")

    lines.append("    jobs    est. time   speedup   efficiency")
    for e in curve:
        marker = "   ← default" if e.jobs == default_jobs else ""
        lines.append(
            f"    {e.jobs:>4d}   {_format_seconds(e.parallel_time_s):>10s}   "
            f"{e.speedup:>5.2f}×        {e.efficiency * 100:>3.0f}%{marker}"
        )
    lines.append("")

    # Too-small-for-parallelism detector: when even the highest job
    # count never beats serial, parallel mode genuinely won't help on
    # this corpus. Tell the user explicitly so they don't read the
    # flat 1.00× column as a bug.
    parallel_never_helps = all(e.speedup <= 1.001 for e in curve)
    if parallel_never_helps and len(curve) > 1:
        lines.append(
            "Note: this corpus is too small for parallelism to pay off."
        )
        lines.append(
            "      Worker spawn cost (~0.15 s each) exceeds the work"
        )
        lines.append(
            "      itself, so lacuna will stay single-process here even"
        )
        lines.append(
            "      at higher --jobs values. Speedup column reads 1.00×"
        )
        lines.append(
            "      across the board because that's the truth, not a bug."
        )
        lines.append("")

    if calibrated:
        when = calibrated_at or "unknown"
        lines.append(
            f"Cost model:    p = {PARALLEL_FRACTION:.2f}, calibrated "
            f"on this machine ({when})."
        )
    else:
        lines.append(
            f"Cost model:    p = {PARALLEL_FRACTION:.2f}, M-series "
            f"baseline (uncalibrated; expect ±2-4× error)."
        )
        lines.append(
            "               Run `lacuna est --recalibrate` for "
            "machine-specific accuracy."
        )
    lines.append(
        "Methodology:   docs/explanation/estimator.md"
    )
    return "\n".join(lines) + "\n"

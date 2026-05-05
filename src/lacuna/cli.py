"""Command-line entry point.

Subcommands:
  init    create lacuna.toml + .lacuna/ in the current directory
  check   scan a project and print gaps (text or JSON)

The TUI (default-when-no-subcommand) and `explain`, `suppress`, `rules`,
`groups`, `stats`, `watch` land in later passes.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

import hashlib
import json

from . import __version__
from .config import Config, find_config
from .entities import Entity, FeatureSet
from .extractors import discover_extractors, extension_dispatch
from .mining import mine, short_id_for
from .output import format_gaps, format_gaps_json
from .parsing import find_source_files
from .selectors import decorator_groups, directory_groups, parent_class_groups
from .storage import StateLock, StateLockError, Storage


_INIT_TEMPLATE = """\
# lacuna configuration. Run `lacuna check` from this directory.
# Every section is optional; defaults are sensible.

[scan]
include   = ["."]
exclude   = []
languages = ["python"]

[mining]
min_confidence = 0.8
min_group_size = 3

[selectors.directory]
enabled     = true
min_members = 3
kind_filter = ["function"]

[selectors.decorator]
enabled     = true
min_members = 3
exclude     = ["@property", "@staticmethod", "@classmethod"]
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lacuna",
        description="Find the holes your code already drew.",
    )
    parser.add_argument("--version", action="version", version=f"lacuna {__version__}")
    sub = parser.add_subparsers(dest="cmd")

    init = sub.add_parser("init", help="Create lacuna.toml + .lacuna/ in the current dir.")
    init.add_argument("path", nargs="?", default=".", help="Where to init (default: cwd)")
    init.add_argument("--force", action="store_true",
                      help="Overwrite an existing lacuna.toml")

    check = sub.add_parser("check", help="Scan a project and print gaps.")
    check.add_argument("path", nargs="?", default=".", help="Project root (default: cwd)")
    check.add_argument("--config", type=Path, default=None,
                       help="Path to lacuna.toml (default: search root upward)")
    check.add_argument("--min-confidence", type=float, default=None,
                       help="Override mining.min_confidence")
    check.add_argument("--min-group-size", type=int, default=None,
                       help="Override mining.min_group_size")
    check.add_argument("--json", action="store_true", dest="as_json",
                       help="Emit machine-readable JSON instead of human text")
    check.add_argument("--quiet", action="store_true",
                       help="Suppress the stats footer (text mode only)")
    check.add_argument("--jobs", "-j", type=int, default=None,
                       metavar="N",
                       help="Parse files across N worker processes "
                            "(default: half of CPU cores)")

    est = sub.add_parser(
        "est",
        aliases=["estimate"],
        help="Estimate cold-scan time without scanning.",
    )
    est.add_argument("path", nargs="?", default=".",
                     help="Project root (default: cwd)")
    est.add_argument("--recalibrate", action="store_true",
                     help="Force re-running the calibration even if a "
                          "fresh cache exists.")
    est.add_argument("--use-synthetic", action="store_true",
                     help="Calibrate against a bundled synthetic Python "
                          "corpus instead of cwd. Useful when the current "
                          "directory is empty or too small for reliable "
                          "calibration.")

    suppress = sub.add_parser(
        "suppress",
        help="Mark a gap as known/intentional so it stops appearing in check.",
    )
    suppress.add_argument("gap_id", nargs="?", default=None,
                          help="Short ('g-7c91234') or full gap id from "
                               "`lacuna check` output")
    suppress.add_argument("--reason", default=None,
                          help="Required unless --list/--remove. Describes why "
                               "this gap is intentional.")
    suppress.add_argument("--remove", action="store_true",
                          help="Remove an existing suppression instead of adding one")
    suppress.add_argument("--list", action="store_true", dest="as_list",
                          help="List current suppressions and exit")
    suppress.add_argument("--path", default=".",
                          help="Project root (default: cwd)")

    args = parser.parse_args(argv)

    if args.cmd is None:
        # No subcommand: launch the TUI when run from a TTY, otherwise
        # fall through to printing help (so piped usage stays sane).
        if sys.stdin.isatty() and sys.stdout.isatty():
            from .tui import run_tui
            root = Path(".").resolve()
            config = _load_config(root, None)
            return run_tui(root, config)
        parser.print_help()
        return 0

    if args.cmd == "init":
        return cmd_init(root=Path(args.path).resolve(), force=args.force)

    if args.cmd == "check":
        root = Path(args.path).resolve()
        config = _load_config(root, args.config)
        # CLI flags override config values for the mining knobs.
        from dataclasses import replace
        mining = config.mining
        if args.min_confidence is not None:
            mining = replace(mining, min_confidence=args.min_confidence)
        if args.min_group_size is not None:
            mining = replace(mining, min_group_size=args.min_group_size)
        config = replace(config, mining=mining)

        return cmd_check(
            root=root,
            config=config,
            quiet=args.quiet,
            as_json=args.as_json,
            jobs=args.jobs,
        )

    if args.cmd in ("est", "estimate"):
        return cmd_est(
            root=Path(args.path).resolve(),
            recalibrate=args.recalibrate,
            use_synthetic=args.use_synthetic,
        )

    if args.cmd == "suppress":
        return cmd_suppress(
            root=Path(args.path).resolve(),
            gap_id=args.gap_id,
            reason=args.reason,
            remove=args.remove,
            as_list=args.as_list,
        )

    return 0


def _load_config(root: Path, explicit: Path | None) -> Config:
    if explicit is not None:
        return Config.from_file(explicit)
    discovered = find_config(root)
    if discovered is not None:
        return Config.from_file(discovered)
    return Config()


def cmd_init(*, root: Path, force: bool) -> int:
    if not root.is_dir():
        print(f"lacuna: not a directory: {root}", file=sys.stderr)
        return 2

    config_path = root / "lacuna.toml"
    state_dir = root / ".lacuna"

    if config_path.exists() and not force:
        print(f"lacuna: {config_path} already exists. Use --force to overwrite.",
              file=sys.stderr)
        return 1

    config_path.write_text(_INIT_TEMPLATE)
    state_dir.mkdir(exist_ok=True)
    (state_dir / ".gitignore").write_text("*\n")
    from .storage import SCHEMA_VERSION as _V
    (state_dir / "version").write_text(f"{_V}\n")

    gitignore = root / ".gitignore"
    if gitignore.exists():
        existing_lines = gitignore.read_text().splitlines()
        if ".lacuna/" not in existing_lines and ".lacuna" not in existing_lines:
            with gitignore.open("a") as fh:
                if existing_lines and existing_lines[-1] != "":
                    fh.write("\n")
                fh.write(".lacuna/\n")

    print(f"Initialized lacuna in {root}")
    print("  - Wrote lacuna.toml")
    print("  - Created .lacuna/ (gitignored)")
    print()

    # First-scan estimate footer. Uses the calibrated model when
    # available; otherwise the uncalibrated baseline. Skips silently
    # if the corpus has no source files yet (empty repo).
    from .config import Config as _Config
    from .estimator import quick_estimate_line
    line = quick_estimate_line(root=root, config=_Config())
    if line is not None:
        print(line)
        print()

    print("Run `lacuna check` to start exploring.")
    return 0


def cmd_check(
    *,
    root: Path,
    config: Config,
    quiet: bool = False,
    as_json: bool = False,
    jobs: int | None = None,
) -> int:
    if not root.is_dir():
        if as_json:
            print(json.dumps({"error": f"not a directory: {root}"}))
        else:
            print(f"lacuna: not a directory: {root}", file=sys.stderr)
        return 2

    from datetime import datetime, timezone
    started = time.perf_counter()
    started_iso = datetime.now(timezone.utc).isoformat()
    state_dir = root / ".lacuna"

    try:
        lock_ctx = StateLock(state_dir / "lockfile").__enter__()
    except StateLockError as exc:
        if as_json:
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"lacuna: {exc}", file=sys.stderr)
        return 2

    try:
        return _run_check(
            root=root,
            state_dir=state_dir,
            config=config,
            quiet=quiet,
            as_json=as_json,
            started=started,
            started_iso=started_iso,
            jobs=jobs,
        )
    finally:
        lock_ctx.__exit__(None, None, None)


def _run_check(
    *,
    root: Path,
    state_dir: Path,
    config: Config,
    quiet: bool,
    as_json: bool,
    started: float,
    started_iso: str,
    jobs: int | None = None,
) -> int:
    extractors = discover_extractors(config.scan.languages)
    if not extractors:
        msg = (
            f"no extractors available for languages={list(config.scan.languages)}"
        )
        if as_json:
            print(json.dumps({"error": msg}))
        else:
            print(f"lacuna: {msg}", file=sys.stderr)
        return 2

    # One-line estimate preamble for interactive text mode.
    # Suppressed in JSON, quiet, and non-TTY contexts to keep
    # CI logs and machine-readable output clean. Gated on stderr
    # (where the line lands) — that way `lacuna check | grep ...`
    # still surfaces the preamble for the human watching the terminal.
    interactive_text_mode = (
        not as_json and not quiet and sys.stderr.isatty()
    )
    if interactive_text_mode:
        from .estimator import quick_estimate_line
        line = quick_estimate_line(root=root, config=config, jobs=jobs)
        if line is not None:
            print(line, file=sys.stderr)

    # Live progress bar for the scan loop, in interactive text mode.
    # We need a total file count to render percent/ETA — get it from
    # walk_corpus, which is sub-second even on the Linux kernel.
    progress_bar = None
    progress_callback = None
    if interactive_text_mode:
        from .estimator import walk_corpus
        from .progress import ProgressBar
        ext_to = extension_dispatch(extractors)
        shape = walk_corpus(root, ext_to)
        if shape.files > 0:
            progress_bar = ProgressBar(total=shape.files, label="Scanning")
            progress_callback = progress_bar.update

    try:
        result = scan_corpus(
            root=root, state_dir=state_dir, config=config,
            started=started, started_iso=started_iso, extractors=extractors,
            jobs=jobs, progress_callback=progress_callback,
        )
    finally:
        if progress_bar is not None:
            progress_bar.finish()
    gaps = result["gaps"]
    rules_by_id = result["rules_by_id"]
    entities = result["entities"]
    scan_stats = result["scan_stats"]

    if as_json:
        print(format_gaps_json(gaps, rules_by_id, entities, scan_stats=scan_stats))
    else:
        print(format_gaps(gaps, rules_by_id, entities,
                          min_confidence=config.mining.min_confidence))
        if not quiet:
            cache_note = (
                f" ({scan_stats['files_unchanged']} unchanged)"
                if scan_stats['files_unchanged'] else ""
            )
            suppressed_note = (
                f", {scan_stats['suppressed']} suppressed"
                if scan_stats['suppressed'] else ""
            )
            print(f"  {len(entities)} entities scanned, "
                  f"{scan_stats['groups']} groups, {scan_stats['rules']} rules in "
                  f"{scan_stats['duration_ms'] / 1000:.2f}s{cache_note}{suppressed_note}")
            print()

    return 1 if gaps else 0


def scan_corpus(
    *,
    root: Path,
    state_dir: Path,
    config: Config,
    started: float | None = None,
    started_iso: str | None = None,
    extractors: dict | None = None,
    jobs: int | None = None,
    progress_callback: Any = None,
) -> dict:
    """Run a full scan + mine cycle and return the result.

    Used by both ``cmd_check`` (which formats and prints) and the TUI
    (which renders widgets). Caller is responsible for the StateLock —
    typically held for the surrounding cmd_check / TUI session.

    ``jobs`` controls parse/extract parallelism. ``None`` means
    auto-detect (half of available cores via :func:`parallel.default_jobs`).

    ``progress_callback``, if provided, is called as
    ``progress_callback(files_done, files_total)`` after each file is
    processed during the scan. Used by ``lacuna check`` to drive a
    live progress bar; passed through to ``_scan_incremental``.
    """
    from datetime import datetime, timezone

    from .parallel import default_jobs

    if started is None:
        started = time.perf_counter()
    if started_iso is None:
        started_iso = datetime.now(timezone.utc).isoformat()
    if extractors is None:
        extractors = discover_extractors(config.scan.languages)
    if jobs is None:
        jobs = default_jobs()

    ext_to_extractor = extension_dispatch(extractors)

    with Storage(state_dir) as storage:
        run_id = storage.begin_run()
        files_seen, files_unchanged = _scan_incremental(
            root, storage, run_id, ext_to_extractor, jobs=jobs,
            progress_callback=progress_callback,
        )
        entities, feature_index = storage.load_all()
        storage.commit()

        items = [(e, feature_index[e.id]) for e in entities.values()]
        groups: list = []
        if config.selectors.directory.enabled:
            groups.extend(directory_groups(
                items,
                min_members=config.selectors.directory.min_members,
                kind_filter=config.selectors.directory.kind_filter,
            ))
        if config.selectors.decorator.enabled:
            groups.extend(decorator_groups(
                items,
                min_members=config.selectors.decorator.min_members,
                exclude=config.selectors.decorator.exclude,
            ))
        if config.selectors.parent_class.enabled:
            groups.extend(parent_class_groups(
                items,
                min_members=config.selectors.parent_class.min_members,
                exclude=config.selectors.parent_class.exclude,
                kind_filter=config.selectors.parent_class.kind_filter,
            ))

        rules: list = []
        gaps: list = []
        for kind in ("decorator", "calls", "parent_class"):
            rs, gs = mine(groups, feature_index,
                          min_confidence=config.mining.min_confidence,
                          feature_kind=kind)
            rules.extend(rs)
            gaps.extend(gs)

        suppressions = storage.load_suppressions()
        suppressed_short_ids = set(suppressions.keys())
        suppressed_full_ids = {
            v["full_id"] for v in suppressions.values() if v["full_id"]
        }
        suppressed_count = 0
        if suppressed_short_ids or suppressed_full_ids:
            kept = []
            for gap in gaps:
                if (
                    gap.short_id in suppressed_short_ids
                    or gap.id in suppressed_full_ids
                ):
                    suppressed_count += 1
                    continue
                kept.append(gap)
            gaps = kept

        elapsed = time.perf_counter() - started

        storage.end_run(
            run_id,
            duration_ms=round(elapsed * 1000, 2),
            entities_scanned=len(entities),
            rules_discovered=len(rules),
            gaps_found=len(gaps),
        )

    rules_by_id = {r.id: r for r in rules}
    scan_stats = {
        "root": str(root),
        "started_at": started_iso,
        "duration_ms": round(elapsed * 1000, 2),
        "entities_scanned": len(entities),
        "files_seen": files_seen,
        "files_unchanged": files_unchanged,
        "groups": len(groups),
        "rules": len(rules),
        "suppressed": suppressed_count,
        "min_confidence": config.mining.min_confidence,
        "min_group_size": config.mining.min_group_size,
    }
    _write_last_run(state_dir, scan_stats, run_id, len(gaps))

    return {
        "entities": entities,
        "feature_index": feature_index,
        "groups": groups,
        "rules": rules,
        "rules_by_id": rules_by_id,
        "gaps": gaps,
        "scan_stats": scan_stats,
    }


def cmd_est(
    *, root: Path, recalibrate: bool, use_synthetic: bool = False,
) -> int:
    """Estimate cold-scan time without actually scanning.

    Walks the corpus, applies the cost model (calibrated if available,
    M-series baseline otherwise), prints the jobs-vs-time table.

    First-run flow: if no calibration cache exists at
    ``~/.lacuna/calibration.json`` and we're attached to a TTY, prompt
    the user once to run calibration. Result is cached forever (until
    invalidated by version/core-count change or ``--recalibrate``).
    """
    from .calibration import (
        calibrated_bps_table,
        calibration_path,
        is_stale,
        load_calibration,
        save_calibration,
    )
    from .estimator import (
        cpu_count_for_estimator,
        format_estimate_report,
        walk_corpus,
    )
    from .parallel import default_jobs

    if not root.is_dir():
        print(f"lacuna: not a directory: {root}", file=sys.stderr)
        return 2

    config = _load_config(root, None)
    extractors = discover_extractors(config.scan.languages)
    if not extractors:
        msg = (
            f"no extractors available for languages={list(config.scan.languages)}"
        )
        print(f"lacuna: {msg}", file=sys.stderr)
        return 2

    ext_to_extractor = extension_dispatch(extractors)
    shape = walk_corpus(root, ext_to_extractor)
    # When --use-synthetic is requested we calibrate on a bundled
    # synthetic corpus regardless of whether `root` has source files.
    # An empty root just means "no estimate report at the end."
    if shape.files == 0 and not use_synthetic:
        print(
            f"lacuna: found no source files under {root} for "
            f"languages={list(config.scan.languages)}.",
            file=sys.stderr,
        )
        return 1

    # ── Calibration: load, decide whether to run or re-run ─────────
    cal_path = calibration_path()
    calibration = None if recalibrate else load_calibration(cal_path)

    interactive = sys.stdin.isatty() and sys.stdout.isatty()

    # Stale check: if cached but version/cores changed, prompt or
    # silently re-run depending on the reason.
    if calibration is not None:
        stale, reason = is_stale(calibration)
        if stale and interactive:
            print(f"Calibration is out of date — {reason}.", file=sys.stderr)
            if _prompt_yn("Re-run calibration?", default=True):
                calibration = None
            # If user declines, keep the stale calibration in use.

    # First-run / explicit-recalibrate flow
    if calibration is None and (recalibrate or interactive):
        calibration = _interactive_calibrate(
            cwd=root, config=config, force=recalibrate,
            use_synthetic=use_synthetic,
        )
        if calibration is not None:
            try:
                save_calibration(calibration, cal_path)
                print(
                    f"Calibration cached at {cal_path}.\n",
                    file=sys.stderr,
                )
            except OSError as e:
                print(
                    f"warning: could not save calibration ({e}); "
                    f"running uncached.\n",
                    file=sys.stderr,
                )
    elif calibration is None and not interactive:
        # Non-TTY (CI, piped output): skip prompts, fall through to
        # uncalibrated output with a note.
        print(
            "(running uncalibrated — pipe to a terminal or run "
            "`lacuna est` interactively to calibrate)\n",
            file=sys.stderr,
        )

    # ── Reality check from prior scan ───────────────────────────────
    observed_cold_scan_s: float | None = None
    last_run_path = root / ".lacuna" / "last_run.json"
    if last_run_path.exists():
        try:
            last = json.loads(last_run_path.read_text())
            if last.get("files_unchanged", -1) == 0 and last.get("duration_ms"):
                observed_cold_scan_s = float(last["duration_ms"]) / 1000.0
        except (OSError, ValueError, KeyError):
            pass

    bps_table = (
        calibrated_bps_table(
            calibration.machine_speed_factor,
            calibration.per_language_bps,
        )
        if calibration is not None else None
    )
    from .estimator import PARALLEL_FRACTION
    p_value = calibration.amdahl_p if calibration is not None else PARALLEL_FRACTION

    # Synthetic-only case: calibration ran but root has no source files,
    # so there's nothing to estimate. Calibration result is what they
    # came for; report it and exit.
    if shape.files == 0:
        print(
            f"\nNo source files in {root} to estimate against. "
            f"Calibration is cached for future `lacuna est` runs."
        )
        return 0

    report = format_estimate_report(
        root=root,
        shape=shape,
        cpu_count=cpu_count_for_estimator(),
        default_jobs=default_jobs(),
        calibrated=calibration is not None,
        calibrated_at=calibration.calibrated_at if calibration else None,
        observed_cold_scan_s=observed_cold_scan_s,
        bps_table=bps_table,
        parallel_fraction=p_value,
    )
    print(report, end="")
    return 0


def _prompt_yn(question: str, *, default: bool = True) -> bool:
    """[Y/n] / [y/N] prompt; default on empty input."""
    suffix = " [Y/n]: " if default else " [y/N]: "
    try:
        response = input(question + suffix).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()  # newline so the next prompt isn't on the same line
        return False
    if not response:
        return default
    return response.startswith("y")


def _interactive_calibrate(
    *, cwd: Path, config: Any, force: bool, use_synthetic: bool = False,
) -> Any:
    """Walk the user through first-run calibration; return CalibrationData
    or None if they decline / it fails.

    When ``use_synthetic`` is True, generate a bundled synthetic Python
    corpus in a temp directory and calibrate against that instead of
    prompting for a path.
    """
    import tempfile

    from .calibration import (
        MIN_CALIBRATION_BYTES,
        MIN_CALIBRATION_FILES,
        make_synthetic_corpus,
        run_calibration,
    )
    from .estimator import _format_seconds, _format_size, serial_time_for, walk_corpus

    if not force:
        print(
            "First time on this machine — run a one-time calibration?\n"
            "This measures your CPU's scanning throughput so estimates\n"
            "match your hardware. Result is cached at\n"
            "~/.lacuna/calibration.json (only runs once)."
        )
        if not _prompt_yn("Calibrate now?", default=True):
            print("Skipping calibration; using M-series baseline.\n")
            return None

    # Synthetic-corpus shortcut: skip the path-prompt loop entirely.
    if use_synthetic:
        with tempfile.TemporaryDirectory(prefix="lacuna-synth-") as tmpd:
            synth_root = make_synthetic_corpus(Path(tmpd) / "corpus")
            print(
                f"\nCalibrating against bundled synthetic corpus "
                f"({len(list(synth_root.glob('*.py')))} files)…"
            )
            try:
                data = run_calibration(corpus_root=synth_root, config=config)
            except (ValueError, KeyboardInterrupt) as e:
                print(f"Calibration failed: {e}")
                return None
        print(
            f"\nCalibration complete:\n"
            f"  speed factor:   {data.machine_speed_factor:.2f}× "
            f"baseline (1.00× = M-series MacBook)\n"
            f"  fitted Amdahl:  p = {data.amdahl_p:.2f}\n"
        )
        return data

    # Pick a corpus path
    target: Path | None = cwd
    while True:
        if target is None:
            return None
        if not target.is_dir():
            print(f"lacuna: not a directory: {target}")
            target = _prompt_path("Enter a calibration corpus path: ")
            if target is None:
                return None
            continue

        from .extractors import discover_extractors, extension_dispatch
        extractors = discover_extractors(config.scan.languages)
        ext_to = extension_dispatch(extractors)
        shape = walk_corpus(target, ext_to)

        if shape.files < MIN_CALIBRATION_FILES or shape.bytes < MIN_CALIBRATION_BYTES:
            print(
                f"\n{target} has only {shape.files} files / "
                f"{_format_size(shape.bytes)} — too small for reliable "
                f"calibration (need ≥ {MIN_CALIBRATION_FILES} files / "
                f"{_format_size(MIN_CALIBRATION_BYTES)}).\n"
                f"Tip: rerun with `--use-synthetic` to calibrate against "
                f"a bundled corpus."
            )
            target = _prompt_path("Enter a different path: ")
            if target is None:
                return None
            continue

        # Show predicted time so the user knows what they're agreeing to
        predicted = serial_time_for(shape.by_language_bytes)
        print(
            f"\nCalibrating against {target}\n"
            f"  files: {shape.files:,d}   "
            f"bytes: {_format_size(shape.bytes)}   "
            f"est. duration: ~{_format_seconds(predicted)} "
            f"(uncalibrated estimate)"
        )
        # When --recalibrate is set, the user already opted into a
        # recalibration explicitly; skip the secondary "Proceed?"
        # prompt so the flow works in non-interactive contexts (CI,
        # piped scripts) without hanging on input.
        if not force:
            if not _prompt_yn("Proceed?", default=True):
                target = _prompt_path(
                    "Enter a different path (or empty to abort): "
                )
                if target is None:
                    return None
                continue

        # Run it
        try:
            print("Running calibration… (press Ctrl+C to abort)")
            data = run_calibration(corpus_root=target, config=config)
        except KeyboardInterrupt:
            print("\nCalibration aborted.")
            return None
        except ValueError as e:
            print(f"Calibration failed: {e}")
            return None

        print(
            f"\nCalibration complete:\n"
            f"  scanned:        {data.calibration_files:,d} files in "
            f"{_format_seconds(data.calibration_duration_s)}\n"
            f"  speed factor:   {data.machine_speed_factor:.2f}× "
            f"baseline (1.00× = M-series MacBook)\n"
        )
        return data


def _prompt_path(question: str) -> Path | None:
    """Read a path from the user; return None on empty/cancel."""
    try:
        raw = input(question).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def cmd_suppress(
    *,
    root: Path,
    gap_id: str | None,
    reason: str | None,
    remove: bool,
    as_list: bool,
) -> int:
    state_dir = root / ".lacuna"
    if not state_dir.is_dir():
        print(f"lacuna: no .lacuna/ in {root}. Run `lacuna check` first.",
              file=sys.stderr)
        return 2

    with Storage(state_dir) as storage:
        if as_list:
            existing = storage.load_suppressions()
            if not existing:
                print("(no suppressions)")
                return 0
            for short_id, info in sorted(existing.items()):
                created = info["created_at"] or ""
                print(f"  {short_id}  {created}")
                print(f"    reason: {info['reason']}")
                if info["full_id"]:
                    print(f"    full:   {info['full_id']}")
                print()
            return 0

        if not gap_id:
            print("lacuna: gap_id required (or --list to show existing).",
                  file=sys.stderr)
            return 2

        if remove:
            removed = storage.remove_suppression(_normalize_short(gap_id))
            if removed:
                print(f"Removed suppression {_normalize_short(gap_id)}.")
                return 0
            print(f"No suppression found for {gap_id!r}.", file=sys.stderr)
            return 1

        if not reason:
            print("lacuna: --reason required when adding a suppression.",
                  file=sys.stderr)
            return 2

        # User may pass either a short id ('g-7c91234') or a full one.
        if gap_id.startswith("g-"):
            short = gap_id
            full: str | None = None
        else:
            short = short_id_for(gap_id)
            full = gap_id

        storage.add_suppression(short_id=short, full_id=full, reason=reason)
        print(f"Suppressed {short}.")
        print(f"  reason: {reason}")
        return 0


def _normalize_short(gap_id: str) -> str:
    """Accept a short id as-is, derive one from a full id."""
    if gap_id.startswith("g-"):
        return gap_id
    return short_id_for(gap_id)


def _scan_incremental(
    root: Path,
    storage: Storage,
    run_id: int,
    ext_to_extractor: dict,
    jobs: int = 1,
    progress_callback: Any = None,
) -> tuple[int, int]:
    """Walk the corpus, reusing cached entities/features for unchanged files.

    Returns ``(files_seen, files_unchanged)`` for the run summary.

    When ``jobs > 1`` and a chunk has enough changed files to amortize
    process startup, parse + extract runs across a worker pool. Storage
    writes always stay on the main process (SQLite is single-writer).
    The worker pool is created lazily on the first chunk that needs it,
    so projects with no changed files (or very few) pay no overhead.

    ``progress_callback``, if provided, is called with ``(n)`` after
    each batch of n files has been processed. The caller-side
    ProgressBar handles total/percent/ETA rendering.
    """
    from .parallel import parse_one, should_parallelize

    cached = storage.all_file_hashes()
    seen_paths: set[str] = set()
    files_unchanged = 0

    # Memory-bounded streaming: we hold at most CHUNK_SIZE files' worth
    # of content + parse results in RAM at once. Above this we drain
    # to storage and start a fresh chunk.
    CHUNK_SIZE = 256

    pool: Any = None
    chunk: list[tuple[str, bytes, str, Any]] = []  # (rel, content, hash, ex)

    def get_pool() -> Any:
        nonlocal pool
        if pool is None:
            from concurrent.futures import ProcessPoolExecutor
            pool = ProcessPoolExecutor(max_workers=jobs)
        return pool

    def flush() -> None:
        if not chunk:
            return
        if should_parallelize(len(chunk), jobs):
            worker_args = [
                (rel, content, ex.language_name)
                for rel, content, _h, ex in chunk
            ]
            results = {
                rel: items
                for rel, items in get_pool().map(
                    parse_one, worker_args, chunksize=4,
                )
            }
        else:
            results = {}
            for rel, content, _h, ex in chunk:
                tree_root = ex.parse(content)
                results[rel] = list(ex.extract(tree_root, rel))

        for rel, _content, current_hash, _ex in chunk:
            storage.delete_entities_for_file(rel)
            new_entities: dict[str, Entity] = {}
            new_features: dict[str, FeatureSet] = {}
            for entity, features in results.get(rel, []):
                new_entities[entity.id] = entity
                new_features[entity.id] = features
            storage.save_entities_and_features(new_entities, new_features)
            storage.upsert_file(rel, current_hash, run_id)
        if progress_callback is not None:
            try:
                progress_callback(len(chunk))
            except Exception:
                pass  # progress UI must never break a scan
        chunk.clear()

    try:
        for path in find_source_files(root, ext_to_extractor.keys()):
            extractor = ext_to_extractor.get(path.suffix.lower())
            if extractor is None:
                continue  # find_source_files already filtered, but be defensive

            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                rel = path.as_posix()
            seen_paths.add(rel)

            try:
                content = path.read_bytes()
            except OSError:
                continue

            current_hash = hashlib.sha256(content).hexdigest()

            if cached.get(rel) == current_hash:
                storage.upsert_file(rel, current_hash, run_id)
                files_unchanged += 1
                if progress_callback is not None:
                    try:
                        progress_callback(1)
                    except Exception:
                        pass
                continue

            chunk.append((rel, content, current_hash, extractor))
            if len(chunk) >= CHUNK_SIZE:
                flush()
        flush()  # final partial chunk
    finally:
        if pool is not None:
            pool.shutdown(wait=True)

    # Tombstone files that disappeared from disk since the last run.
    for stale in set(cached) - seen_paths:
        storage.delete_file(stale)

    return len(seen_paths), files_unchanged


def _write_last_run(
    state_dir: Path, scan_stats: dict, run_id: int, gaps_found: int
) -> None:
    summary = {
        "run_id": run_id,
        "started_at": scan_stats.get("started_at"),  # populated by caller if desired
        "duration_ms": scan_stats["duration_ms"],
        "entities_scanned": scan_stats["entities_scanned"],
        "files_seen": scan_stats["files_seen"],
        "files_unchanged": scan_stats["files_unchanged"],
        "rules_discovered": scan_stats["rules"],
        "gaps_found": gaps_found,
    }
    (state_dir / "last_run.json").write_text(
        json.dumps(summary, indent=2) + "\n"
    )

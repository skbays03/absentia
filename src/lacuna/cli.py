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
from ._console import stderr_console, stdout_console
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
# Languages to scan. Omitting this key enables every built-in
# extractor (python, javascript, typescript, tsx, rust, go, java,
# ruby, csharp, swift, c, cpp, php, kotlin, scala, lua, bash).
# Set explicitly to restrict — e.g. languages = ["python", "rust"].
# languages = ["python"]

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


_SUBCOMMANDS = frozenset({"init", "check", "est", "estimate", "suppress"})


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    # Shorthand: `lacuna <path>` — if the first arg isn't a known
    # subcommand or a flag, and it points at an existing directory,
    # treat it as "open the TUI in that path." This is purely a UX
    # convenience; everything still works via the explicit subcommands.
    tui_path: Path | None = None
    if len(argv) == 1 and argv[0] not in _SUBCOMMANDS and not argv[0].startswith("-"):
        candidate = Path(argv[0]).expanduser()
        if candidate.is_dir():
            tui_path = candidate.resolve()
            argv = []  # fall through to the no-subcommand branch below

    parser = argparse.ArgumentParser(
        prog="lacuna",
        description="Find the holes your code already drew.",
        epilog=(
            "Quick reference:\n"
            "  lacuna                          open the TUI in the current directory\n"
            "  lacuna PATH                     open the TUI in PATH (e.g. lacuna ~/myrepo)\n"
            "  lacuna init                     bootstrap a project here\n"
            "  lacuna check                    batch scan, print gaps, exit non-zero on failure\n"
            "  lacuna check --jobs N           override worker count (default: half of cores)\n"
            "  lacuna est                      estimate cold-scan time without scanning\n"
            "  lacuna est --recalibrate        force fresh calibration on this machine\n"
            "  lacuna est --use-synthetic      calibrate against bundled corpus (empty cwd OK)\n"
            "  lacuna suppress GAP_ID          mark a gap as intentional\n"
            "  lacuna suppress --list          list current suppressions\n"
            "  lacuna --jobs-default N         pin default worker count (0 = auto cpu/2)\n"
            "  lacuna --purge [PATH]           delete .lacuna/ from PATH (default: cwd)\n"
            "  lacuna --purge-all              delete every .lacuna/ under $HOME + machine cache\n"
            "\n"
            "Each subcommand has its own --help with the full flag list:\n"
            "  lacuna check --help · lacuna est --help · lacuna suppress --help\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"lacuna {__version__}")
    parser.add_argument(
        "--purge",
        nargs="?",
        const=".",
        default=None,
        metavar="PATH",
        help="Remove lacuna state (.lacuna/) from PATH (default: cwd). "
             "Lacuna config files (lacuna.toml) are left in place.",
    )
    parser.add_argument(
        "--purge-all",
        action="store_true",
        help="Remove every lacuna state directory under your home + the "
             "machine-wide calibration cache. Confirms before deleting.",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip the [y/N] confirmation prompt on --purge / --purge-all. "
             "Use only when you're sure (and ideally in a script).",
    )
    parser.add_argument(
        "--jobs-default",
        type=int,
        default=None,
        metavar="N",
        help="Pin the default worker count for `lacuna check` so future "
             "scans use N workers without needing `--jobs N` each time. "
             "Saved to ~/.lacuna/settings.json. Pass 0 to revert to auto "
             "(half of cpu cores). If N exceeds your detected core count "
             "you'll be re-prompted, since over-subscribing usually slows "
             "the scan.",
    )
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

    # Top-level purge / settings flags run before subcommand dispatch.
    if args.purge_all:
        return cmd_purge_all(confirm=not args.yes)
    if args.purge is not None:
        return cmd_purge(
            Path(args.purge).expanduser().resolve(),
            confirm=not args.yes,
        )
    if args.jobs_default is not None:
        return cmd_jobs_default(args.jobs_default, confirm=not args.yes)

    if args.cmd is None:
        # No subcommand: launch the TUI when run from a TTY, otherwise
        # fall through to printing help (so piped usage stays sane).
        # ``tui_path`` was populated above when the user passed
        # ``lacuna <path>`` — otherwise default to cwd.
        if sys.stdin.isatty() and sys.stdout.isatty():
            from .tui import run_tui
            root = tui_path if tui_path is not None else Path(".").resolve()
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
        stderr_console.print(f"[red]lacuna:[/] not a directory: [cyan]{root}[/]")
        return 2

    config_path = root / "lacuna.toml"
    state_dir = root / ".lacuna"

    if config_path.exists() and not force:
        stderr_console.print(
            f"[red]lacuna:[/] [cyan]{config_path}[/] already exists. "
            f"Use [bold]--force[/] to overwrite."
        )
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

    stdout_console.print(
        f"[bright_green]✓[/] Initialized lacuna in [cyan]{root}[/]"
    )
    stdout_console.print("  - Wrote [cyan]lacuna.toml[/]")
    stdout_console.print("  - Created [cyan].lacuna/[/] [dim](gitignored)[/]")
    stdout_console.print()

    # First-scan estimate footer. Uses the calibrated model when
    # available; otherwise the uncalibrated baseline. Skips silently
    # if the corpus has no source files yet (empty repo).
    from .config import Config as _Config
    from .estimator import quick_estimate_line
    line = quick_estimate_line(root=root, config=_Config())
    if line is not None:
        print(line)
        print()

    stdout_console.print("Run [bold cyan]`lacuna check`[/] to start exploring.")
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
            stderr_console.print(f"[red]lacuna:[/] not a directory: [cyan]{root}[/]")
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
            stderr_console.print(f"[red]lacuna:[/] {exc}")
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
            stderr_console.print(f"[red]lacuna:[/] {msg}")
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

    result = scan_corpus(
        root=root, state_dir=state_dir, config=config,
        started=started, started_iso=started_iso, extractors=extractors,
        jobs=jobs, interactive=interactive_text_mode,
    )
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
    interactive: bool = False,
) -> dict:
    """Run a full scan + mine cycle and return the result.

    Used by both ``cmd_check`` (which formats and prints) and the TUI
    (which renders widgets). Caller is responsible for the StateLock —
    typically held for the surrounding cmd_check / TUI session.

    ``jobs`` controls parse/extract parallelism. ``None`` means
    auto-detect (half of available cores via :func:`parallel.default_jobs`).

    ``progress_callback``, if provided, is called as
    ``progress_callback(files_done, files_total)`` after each file is
    processed during the scan. The TUI uses this to drive its own
    widgets. Mutually exclusive with ``interactive=True``.

    ``interactive`` controls per-stage TTY progress UI. When True (set
    by ``cmd_check`` in interactive text mode), each pipeline stage
    (walk, parse, store, mine, finalize) gets its own indicator that
    finishes with a ✓ summary line + elapsed time — so the user can
    see which stage just took N seconds and a hang is immediately
    diagnosable. The TUI passes ``interactive=False`` and drives its
    own widgets via ``progress_callback``.
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

    # Per-stage wall times — populated whether or not we're rendering
    # spinners. Persisted into last_run.json so `lacuna est` can show a
    # real breakdown (parse N s + mine M s + finalize K s = total) and
    # predict full check time, not just the parse stage.
    stage_durations: dict[str, float] = {
        "walk": 0.0, "parse": 0.0, "store": 0.0,
        "mine": 0.0, "finalize": 0.0,
    }

    # ── Walk stage: count files up-front so the parse bar has a
    # total. Skipped when the caller is driving its own progress UI
    # (TUI). Even at 65 k files this is sub-second on Mac and ~1–2 s
    # on the kernel; the spinner makes that wait visible.
    parse_bar = None
    if interactive:
        from .estimator import _format_size, walk_corpus
        from .progress import ProgressBar, Spinner, _format_time, spinning

        walk_spinner = Spinner(label="Walking corpus")
        walk_started = time.perf_counter()
        with spinning(walk_spinner):
            shape = walk_corpus(
                root, ext_to_extractor,
                on_file=walk_spinner.set_current_item,
            )
        stage_durations["walk"] = time.perf_counter() - walk_started
        walk_spinner.finish(
            end_message=(
                f"Walked corpus  ·  {shape.files:,d} files, "
                f"{_format_size(shape.bytes)}  ·  "
                f"{_format_time(stage_durations['walk'])}"
            )
        )

        if shape.files > 0:
            parse_bar = ProgressBar(total=shape.files, label="Scanning")
            progress_callback = parse_bar.update

    with Storage(state_dir) as storage:
        run_id = storage.begin_run()
        parse_started = time.perf_counter()
        try:
            files_seen, files_unchanged = _scan_incremental(
                root, storage, run_id, ext_to_extractor, jobs=jobs,
                progress_callback=progress_callback,
            )
        finally:
            if parse_bar is not None:
                parse_bar.finish()
        stage_durations["parse"] = time.perf_counter() - parse_started

        # ── Storage-commit stage ──────────────────────────────────
        store_started = time.perf_counter()
        if interactive:
            store_spinner = Spinner(label="Loading entity store")
            with spinning(store_spinner):
                entities, feature_index = storage.load_all()
                storage.commit()
            stage_durations["store"] = time.perf_counter() - store_started
            store_spinner.finish(
                end_message=(
                    f"Loaded store  ·  {len(entities):,d} entities  ·  "
                    f"{_format_time(stage_durations['store'])}"
                )
            )
        else:
            entities, feature_index = storage.load_all()
            storage.commit()
            stage_durations["store"] = time.perf_counter() - store_started

        # Corpus-level feature enrichment: features that need to know
        # about the whole corpus (e.g. sibling_test, which checks
        # whether a matching test entity exists). Runs in memory only;
        # not persisted because the result depends on the full set of
        # entities, not on any single file.
        from .enrichment import enrich_all
        enrich_all(entities, feature_index)

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

        # ── Mining stage ──────────────────────────────────────────
        # Mining strategies are independent: each takes the same
        # read-only inputs and produces its own (rules, gaps). Run
        # them in parallel via threads. Pure-Python loops yield the
        # GIL on time slice, so we get ~10-30% wall-clock improvement
        # from interleaving even on the GIL-bound work; for the
        # parts that release the GIL (regex in series, frozenset
        # operations) the gain is bigger. Threads not processes:
        # zero pickle cost, zero spawn cost, all tasks share the
        # same in-memory entities + feature_index + groups.
        from concurrent.futures import ThreadPoolExecutor

        from .symmetry import find_call_pair_gaps, find_symmetry_gaps
        from .series import find_series_gaps

        rules: list = []
        gaps: list = []

        def _mine_kind(kind: str) -> tuple[list, list]:
            rs, gs = mine(
                groups, feature_index,
                min_confidence=config.mining.min_confidence,
                feature_kind=kind,
            )
            return list(rs), list(gs)

        # Cap mining workers at the parallel-fraction sweet spot
        # (4 — Amdahl's `p` doesn't reward more on this stage).
        # Always at least 1 even on a single-core machine.
        mining_workers = max(1, min(4, jobs))

        # The mining tasks; a list of (label, callable) we'll submit
        # as one batch. Labels surface in the spinner sub-line so the
        # user can see which strategy just finished — and, if anything
        # hangs, which one is the culprit.
        mining_tasks: list[tuple[str, Any]] = [
            ("frequency:decorator",     lambda: _mine_kind("decorator")),
            ("frequency:calls",         lambda: _mine_kind("calls")),
            ("frequency:parent_class",  lambda: _mine_kind("parent_class")),
            ("frequency:sibling_test",  lambda: _mine_kind("sibling_test")),
            ("symmetry pairs",          lambda: find_symmetry_gaps(entities)),
            ("call-pair",               lambda: find_call_pair_gaps(entities, feature_index)),
            ("series",                  lambda: find_series_gaps(entities)),
        ]

        mine_started = time.perf_counter()
        if interactive:
            mine_spinner = Spinner(label="Mining rules")
            done = 0
            total_tasks = len(mining_tasks)
            with spinning(mine_spinner), \
                    ThreadPoolExecutor(max_workers=mining_workers) as ex:
                fut_to_label = {
                    ex.submit(fn): label for label, fn in mining_tasks
                }
                from concurrent.futures import as_completed
                for fut in as_completed(fut_to_label):
                    rs, gs = fut.result()
                    rules.extend(rs)
                    gaps.extend(gs)
                    done += 1
                    mine_spinner.set_current_item(
                        f"{done}/{total_tasks} done · last: "
                        f"{fut_to_label[fut]} · {len(rules):,d} rules so far"
                    )
            stage_durations["mine"] = time.perf_counter() - mine_started
            mine_spinner.finish(
                end_message=(
                    f"Mined rules  ·  {len(rules):,d} rules, "
                    f"{len(gaps):,d} candidate gaps  ·  "
                    f"{_format_time(stage_durations['mine'])}"
                )
            )
        else:
            with ThreadPoolExecutor(max_workers=mining_workers) as ex:
                futures = [ex.submit(fn) for _, fn in mining_tasks]
                for fut in futures:
                    rs, gs = fut.result()
                    rules.extend(rs)
                    gaps.extend(gs)
            stage_durations["mine"] = time.perf_counter() - mine_started

        # ── Finalize stage: dedup, suppress, end_run ──────────────
        finalize_started = time.perf_counter()
        if interactive:
            final_spinner = Spinner(label="Finalizing")
            final_ctx: Any = spinning(final_spinner)
            final_ctx.__enter__()
        else:
            final_spinner = None
            final_ctx = None

        # Dedupe gaps across mining strategies. Frequency mining,
        # symmetry pairs, and call-pair mining can each independently
        # flag the same entity for the same missing thing
        # (e.g. "leaky is missing bus.unsubscribe"). Users only want
        # to see it once. Highest-confidence rule wins.
        rules_by_id_for_dedup = {r.id: r for r in rules}
        gaps_sorted = sorted(
            gaps,
            key=lambda g: (
                -rules_by_id_for_dedup[g.rule_id].confidence
                if g.rule_id in rules_by_id_for_dedup else 0
            ),
        )
        seen_render_keys: set[tuple[str, str]] = set()
        deduped: list = []
        for gap in gaps_sorted:
            rule = rules_by_id_for_dedup.get(gap.rule_id)
            if rule is None:
                continue
            key = (gap.entity_id, rule.feature_value)
            if key in seen_render_keys:
                continue
            seen_render_keys.add(key)
            deduped.append(gap)
        gaps = deduped

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

        stage_durations["finalize"] = time.perf_counter() - finalize_started
        if final_spinner is not None and final_ctx is not None:
            final_ctx.__exit__(None, None, None)
            suppress_note = (
                f", {suppressed_count} suppressed"
                if suppressed_count else ""
            )
            final_spinner.finish(
                end_message=(
                    f"Finalized  ·  {len(gaps):,d} gaps after dedup"
                    f"{suppress_note}  ·  "
                    f"{_format_time(stage_durations['finalize'])}"
                )
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
        "stage_durations_ms": {
            stage: round(secs * 1000, 2)
            for stage, secs in stage_durations.items()
        },
        "jobs": jobs,
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


def cmd_purge(root: Path, *, confirm: bool = True) -> int:
    """Remove lacuna state from a single project root.

    Only removes ``.lacuna/`` (the gitignored state directory).
    Leaves ``lacuna.toml`` in place — that's a versioned config the
    user might want to keep for re-running scans later.

    When ``confirm`` is True (the default), prompts ``[y/N]`` with a
    disclaimer of what's about to be deleted. Refuses outright in
    non-interactive contexts unless ``confirm=False`` (set by
    ``--yes``/``-y``).
    """
    import shutil

    if not root.is_dir():
        stderr_console.print(f"[red]lacuna:[/] not a directory: [cyan]{root}[/]")
        return 2

    target = root / ".lacuna"
    if not target.exists():
        stdout_console.print(
            f"[dim]lacuna: no .lacuna/ directory at [cyan]{root}[/]; "
            f"nothing to purge.[/]"
        )
        return 0
    if not target.is_dir():
        stderr_console.print(
            f"[red]lacuna:[/] [cyan]{target}[/] exists but isn't a directory; "
            f"refusing to remove."
        )
        return 1

    # Sanity check: verify it looks like a lacuna state dir before deleting.
    # A real lacuna .lacuna/ has at least a `version` file and `state.db`.
    looks_lacuna = (
        (target / "version").exists() or (target / "state.db").exists()
    )
    if not looks_lacuna:
        stderr_console.print(
            f"[red]lacuna:[/] [cyan]{target}[/] doesn't look like a lacuna "
            f"state directory (no version or state.db). Refusing to remove. "
            f"Delete manually if you're sure."
        )
        return 1

    if confirm:
        print("About to delete:")
        print(f"  {target}")
        print()
        print("This is the per-project state directory. It contains:")
        print("  - state.db   — entity cache + feature index + scan history")
        print("  - suppressions you've added with `lacuna suppress`")
        print("  - last_run.json — timing/stats from the previous scan")
        print()
        print("Your source code and lacuna.toml are NOT touched. The next")
        print("scan will be a full cold scan instead of incremental.")
        print()
        if not sys.stdin.isatty():
            print(
                "lacuna --purge: refusing to delete in non-interactive "
                "context. Re-run from a terminal, or pass --yes to skip "
                "this prompt.",
                file=sys.stderr,
            )
            return 1
        try:
            response = input("Remove? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 1
        if not response.startswith("y"):
            stdout_console.print("[yellow]Aborted; nothing removed.[/]")
            return 0

    shutil.rmtree(target)
    stdout_console.print(f"[bright_green]✓[/] Removed [cyan]{target}[/]")
    if (root / "lacuna.toml").exists():
        print(
            f"  (lacuna.toml at {root} kept; "
            f"delete manually if you also want to remove config)"
        )
    return 0


def cmd_purge_all(*, confirm: bool = True) -> int:
    """Remove every lacuna state dir under $HOME + the machine-wide cache.

    Confirms before deleting; prints what will be removed first so the
    user can sanity-check. ``confirm=False`` (set by ``--yes``/``-y``)
    skips the prompt — use only in scripted contexts.
    """
    import shutil

    home = Path.home()

    # Skip dirs that almost certainly aren't going to host a lacuna state
    # and would otherwise slow the walk dramatically.
    SKIP_DIR_NAMES = {
        ".git", "node_modules", ".venv", "venv", "__pycache__",
        "site-packages", "dist", "build", ".cache", ".npm", ".cargo",
        ".rustup", "target", ".gradle", ".m2", ".tox", ".mypy_cache",
        ".pytest_cache", ".ruff_cache", "Library",  # macOS app data
    }

    def is_skip_descendant(path: Path) -> bool:
        return any(part in SKIP_DIR_NAMES for part in path.parts)

    project_state_dirs: list[Path] = []
    machine_cache = home / ".lacuna"

    # rglob over $HOME can be 10–60 s on a packed home dir; use a
    # spinner so the user sees the tool is alive.
    from .progress import Spinner, spinning
    spinner = Spinner(label=f"Scanning {home} for lacuna state")
    try:
        with spinning(spinner):
            for path in home.rglob(".lacuna"):
                # Show paths as the rglob visits them, even ones we skip,
                # so the user sees the walk progressing.
                spinner.set_current_item(str(path))
                if not path.is_dir():
                    continue
                if is_skip_descendant(path):
                    continue
                if path == machine_cache:
                    continue  # handled separately below
                # Verify it looks like a project state dir
                if (path / "version").exists() or (path / "state.db").exists():
                    project_state_dirs.append(path)
    except OSError as e:
        spinner.finish()
        stderr_console.print(f"[red]lacuna:[/] scan failed: {e}")
        return 2
    spinner.finish(end_message=f"Scan complete ({len(project_state_dirs)} project state dirs found)")

    # Machine-wide cache (different shape — has calibration.json, no version)
    machine_cache_present = (
        machine_cache.is_dir()
        and (machine_cache / "calibration.json").exists()
    )

    if not project_state_dirs and not machine_cache_present:
        stdout_console.print("[dim]No lacuna state found.[/]")
        return 0

    print()
    print("About to delete:")
    if project_state_dirs:
        print(f"\n  Project state directories ({len(project_state_dirs)}):")
        for d in sorted(project_state_dirs):
            print(f"    {d}")
    if machine_cache_present:
        print("\n  Machine-wide cache:")
        print(f"    {machine_cache}/calibration.json")
    print()
    print("This will:")
    print("  - Wipe per-project entity caches, feature indexes, and")
    print("    suppressions across every project listed above.")
    print("  - Wipe the machine-wide calibration cache, so the next")
    print("    `lacuna est` will re-prompt for first-run calibration.")
    print()
    print("Source code, lacuna.toml configs, and the lacuna binary itself")
    print("are NOT touched.")
    print()

    if confirm:
        if not sys.stdin.isatty():
            print(
                "lacuna --purge-all: refusing to delete in non-interactive "
                "context. Re-run from a terminal, or pass --yes to skip "
                "this prompt.",
                file=sys.stderr,
            )
            return 1

        try:
            response = input("Remove all of these? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 1
        if not response.startswith("y"):
            stdout_console.print("[yellow]Aborted; nothing removed.[/]")
            return 0

    removed = 0
    failed = 0
    for d in project_state_dirs:
        try:
            shutil.rmtree(d)
            removed += 1
        except OSError as e:
            stderr_console.print(f"  [red]failed:[/] [cyan]{d}[/] ({e})")
            failed += 1

    if machine_cache_present:
        try:
            shutil.rmtree(machine_cache)
            removed += 1
        except OSError as e:
            stderr_console.print(
                f"  [red]failed:[/] [cyan]{machine_cache}[/] ({e})"
            )
            failed += 1

    if failed:
        stdout_console.print(
            f"\n[bright_green]✓[/] Removed [bold]{removed}[/] location(s). "
            f"[red]{failed} failed.[/]"
        )
    else:
        stdout_console.print(
            f"\n[bright_green]✓[/] Removed [bold]{removed}[/] location(s)."
        )
    return 0 if failed == 0 else 1


def cmd_jobs_default(n: int, *, confirm: bool = True) -> int:
    """Pin the default worker count to ``~/.lacuna/settings.json``.

    ``n == 0`` resets to auto (cpu_count // 2). ``n < 0`` is an error.
    ``n > detected_cores`` triggers a re-prompt — over-subscribing the
    machine usually *slows* the scan because workers contend for CPU,
    but a power user may have a reason (background-only scans, etc.),
    so we ask rather than refuse outright. ``--yes`` skips the prompt.
    """
    from .parallel import detected_cores
    from .settings import Settings, load_settings, save_settings, settings_path

    if n < 0:
        stderr_console.print(
            f"[red]lacuna:[/] --jobs-default must be ≥ 0, got [bold]{n}[/]."
        )
        return 2

    cores = detected_cores()
    auto = max(1, cores // 2)

    if n == 0:
        # Reset to auto. Save settings with jobs_default=None so
        # default_jobs() falls through to the cpu//2 heuristic.
        save_settings(Settings(jobs_default=None))
        stdout_console.print(
            f"[bright_green]✓[/] Default jobs reset to auto "
            f"([bold]{auto}[/] = half of {cores} cores)."
        )
        stdout_console.print(f"  saved to [cyan]{settings_path()}[/]")
        return 0

    chosen = n
    if chosen > cores:
        stderr_console.print(
            f"[yellow]warning:[/] [bold]{chosen}[/] exceeds detected core "
            f"count ([bold]{cores}[/]).\n"
            f"Over-subscribing usually [dim]slows[/] scans because workers "
            f"contend for CPU."
        )
        if confirm:
            if not sys.stdin.isatty():
                stderr_console.print(
                    f"[red]lacuna:[/] non-interactive context; refusing to "
                    f"set [bold]{chosen}[/] over [bold]{cores}[/] without "
                    f"confirmation. Pass [bold]--yes[/] to override, or "
                    f"re-run from a terminal."
                )
                return 2
            try:
                response = input(
                    f"Continue with {chosen}, enter a new value "
                    f"(1–{cores}), or blank to abort: "
                ).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 1
            if not response:
                stdout_console.print("[yellow]Aborted; nothing changed.[/]")
                return 0
            if not response.isdigit():
                # Treat any non-digit (yes/y/etc.) as "keep the
                # over-subscribed value the user already typed".
                if response.lower() not in ("y", "yes"):
                    stderr_console.print(
                        f"[red]lacuna:[/] not a number: {response!r}"
                    )
                    return 2
            else:
                alt = int(response)
                if alt < 1:
                    stderr_console.print(
                        f"[red]lacuna:[/] jobs must be ≥ 1, got {alt}."
                    )
                    return 2
                chosen = alt
                if chosen > cores:
                    stdout_console.print(
                        f"[dim]ok — saving {chosen} despite only "
                        f"{cores} cores.[/]"
                    )

    current = load_settings()
    save_settings(Settings(jobs_default=chosen))
    prev = (
        f" [dim](was {current.jobs_default})[/]"
        if current.jobs_default is not None
        else " [dim](was auto)[/]"
    )
    stdout_console.print(
        f"[bright_green]✓[/] Default jobs set to [bold]{chosen}[/]{prev}."
    )
    stdout_console.print(f"  saved to [cyan]{settings_path()}[/]")
    return 0


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
        stderr_console.print(f"[red]lacuna:[/] not a directory: [cyan]{root}[/]")
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

    # Spinner during the corpus walk — sub-second on small projects but
    # ~1–2 s on the Linux kernel; without it the tool looks frozen on
    # big inputs. Per-file callback feeds the spinner's sub-line so
    # the user sees actual paths flash by during the walk.
    from .progress import Spinner, spinning
    spinner = Spinner(label=f"Walking {root}")
    with spinning(spinner):
        shape = walk_corpus(
            root, ext_to_extractor, on_file=spinner.set_current_item,
        )
    spinner.finish()

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
            stderr_console.print(
                f"[yellow]Calibration is out of date[/] — {reason}."
            )
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
                stderr_console.print(
                    f"[bright_green]✓[/] Calibration cached at "
                    f"[cyan]{cal_path}[/].\n"
                )
            except OSError as e:
                stderr_console.print(
                    f"[yellow]warning:[/] could not save calibration ({e}); "
                    f"running uncached.\n"
                )
    elif calibration is None and not interactive:
        # Non-TTY (CI, piped output): skip prompts, fall through to
        # uncalibrated output with a note.
        stderr_console.print(
            "[dim](running uncalibrated — pipe to a terminal or run "
            "`lacuna est` interactively to calibrate)[/]\n"
        )

    # ── Reality check from prior scan ───────────────────────────────
    observed_cold_scan_s: float | None = None
    observed_stage_durations: dict[str, float] | None = None
    observed_jobs: int | None = None
    last_run_path = root / ".lacuna" / "last_run.json"
    if last_run_path.exists():
        try:
            last = json.loads(last_run_path.read_text())
            if last.get("files_unchanged", -1) == 0 and last.get("duration_ms"):
                observed_cold_scan_s = float(last["duration_ms"]) / 1000.0
                stage_ms = last.get("stage_durations_ms")
                if isinstance(stage_ms, dict):
                    observed_stage_durations = {
                        k: float(v) / 1000.0
                        for k, v in stage_ms.items()
                        if isinstance(v, (int, float))
                    }
                if isinstance(last.get("jobs"), int):
                    observed_jobs = last["jobs"]
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

    # Calibrated mining-tail estimate for this corpus (used when no
    # observed last_run.json data exists). Linear extrapolation:
    # mining_spb measured during calibration × current corpus bytes.
    # When observed_stage_durations is also present, that wins inside
    # format_estimate_report — observed beats modeled.
    model_mining_tail_s: float | None = None
    if (
        calibration is not None
        and calibration.mining_seconds_per_byte > 0
        and shape.bytes > 0
    ):
        model_mining_tail_s = (
            shape.bytes * calibration.mining_seconds_per_byte
        )

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
        observed_stage_durations=observed_stage_durations,
        observed_jobs=observed_jobs,
        model_mining_tail_s=model_mining_tail_s,
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
        stdout_console.print(
            "[bold cyan]First time on this machine[/] — run a one-time "
            "calibration?\nThis measures your CPU's scanning throughput "
            "so estimates\nmatch your hardware. Result is cached at\n"
            "[cyan]~/.lacuna/calibration.json[/] (only runs once)."
        )
        if not _prompt_yn("Calibrate now?", default=True):
            stdout_console.print(
                "[dim]Skipping calibration; using M-series baseline.[/]\n"
            )
            return None

    # Synthetic-corpus shortcut: skip the path-prompt loop entirely.
    if use_synthetic:
        with tempfile.TemporaryDirectory(prefix="lacuna-synth-") as tmpd:
            synth_root = make_synthetic_corpus(Path(tmpd) / "corpus")
            stdout_console.print(
                f"\nCalibrating against bundled synthetic corpus "
                f"([bold]{len(list(synth_root.glob('*.py')))}[/] files)…"
            )
            try:
                data = run_calibration(corpus_root=synth_root, config=config)
            except (ValueError, KeyboardInterrupt) as e:
                stderr_console.print(f"[red]Calibration failed:[/] {e}")
                return None
        stdout_console.print(
            f"\n[bright_green]✓[/] Calibration complete:\n"
            f"  speed factor:   [bold]{data.machine_speed_factor:.2f}×[/] "
            f"baseline (1.00× = M-series MacBook)\n"
            f"  fitted Amdahl:  p = [bold]{data.amdahl_p:.2f}[/]\n"
        )
        return data

    # Pick a corpus path
    target: Path | None = cwd
    while True:
        if target is None:
            return None
        if not target.is_dir():
            stderr_console.print(
                f"[red]lacuna:[/] not a directory: [cyan]{target}[/]"
            )
            target = _prompt_path("Enter a calibration corpus path: ")
            if target is None:
                return None
            continue

        from .extractors import discover_extractors, extension_dispatch
        extractors = discover_extractors(config.scan.languages)
        ext_to = extension_dispatch(extractors)
        shape = walk_corpus(target, ext_to)

        if shape.files < MIN_CALIBRATION_FILES or shape.bytes < MIN_CALIBRATION_BYTES:
            stdout_console.print(
                f"\n[yellow]{target} has only {shape.files} files / "
                f"{_format_size(shape.bytes)}[/] — too small for reliable "
                f"calibration (need ≥ {MIN_CALIBRATION_FILES} files / "
                f"{_format_size(MIN_CALIBRATION_BYTES)}).\n"
                f"[dim]Tip:[/] rerun with [bold cyan]`--use-synthetic`[/] "
                f"to calibrate against a bundled corpus."
            )
            target = _prompt_path("Enter a different path: ")
            if target is None:
                return None
            continue

        # Show predicted time so the user knows what they're agreeing to
        predicted = serial_time_for(shape.by_language_bytes)
        stdout_console.print(
            f"\nCalibrating against [cyan]{target}[/]\n"
            f"  files: [bold]{shape.files:,d}[/]   "
            f"bytes: [bold]{_format_size(shape.bytes)}[/]   "
            f"est. duration: [green]~{_format_seconds(predicted)}[/] "
            f"[dim](uncalibrated estimate)[/]"
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
            stdout_console.print(
                "[dim]Running calibration… (press Ctrl+C to abort)[/]"
            )
            data = run_calibration(corpus_root=target, config=config)
        except KeyboardInterrupt:
            stdout_console.print("\n[yellow]Calibration aborted.[/]")
            return None
        except ValueError as e:
            stderr_console.print(f"[red]Calibration failed:[/] {e}")
            return None

        stdout_console.print(
            f"\n[bright_green]✓[/] Calibration complete:\n"
            f"  scanned:        [bold]{data.calibration_files:,d}[/] files in "
            f"[green]{_format_seconds(data.calibration_duration_s)}[/]\n"
            f"  speed factor:   [bold]{data.machine_speed_factor:.2f}×[/] "
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
        stderr_console.print(
            f"[red]lacuna:[/] no [cyan].lacuna/[/] in [cyan]{root}[/]. "
            f"Run [bold cyan]`lacuna check`[/] first."
        )
        return 2

    with Storage(state_dir) as storage:
        if as_list:
            existing = storage.load_suppressions()
            if not existing:
                stdout_console.print("[dim](no suppressions)[/]")
                return 0
            for short_id, info in sorted(existing.items()):
                created = info["created_at"] or ""
                stdout_console.print(
                    f"  [bold]{short_id}[/]  [dim]{created}[/]"
                )
                stdout_console.print(f"    reason: {info['reason']}")
                if info["full_id"]:
                    stdout_console.print(
                        f"    full:   [dim]{info['full_id']}[/]"
                    )
                stdout_console.print()
            return 0

        if not gap_id:
            stderr_console.print(
                "[red]lacuna:[/] gap_id required "
                "(or [bold]--list[/] to show existing)."
            )
            return 2

        if remove:
            removed = storage.remove_suppression(_normalize_short(gap_id))
            if removed:
                stdout_console.print(
                    f"[bright_green]✓[/] Removed suppression "
                    f"[bold]{_normalize_short(gap_id)}[/]."
                )
                return 0
            stderr_console.print(
                f"[red]No suppression found for[/] [bold]{gap_id!r}[/]."
            )
            return 1

        if not reason:
            stderr_console.print(
                "[red]lacuna:[/] [bold]--reason[/] required "
                "when adding a suppression."
            )
            return 2

        # User may pass either a short id ('g-7c91234') or a full one.
        if gap_id.startswith("g-"):
            short = gap_id
            full: str | None = None
        else:
            short = short_id_for(gap_id)
            full = gap_id

        storage.add_suppression(short_id=short, full_id=full, reason=reason)
        stdout_console.print(
            f"[bright_green]✓[/] Suppressed [bold]{short}[/]."
        )
        stdout_console.print(f"  reason: {reason}")
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

    def _emit_progress(rel: str, count_increment: int) -> None:
        """Best-effort progress update. count_increment may be 0 to
        update only the displayed item without advancing the bar."""
        if progress_callback is None:
            return
        try:
            progress_callback(count_increment, item=rel)
        except TypeError:
            # Older callbacks accept only the count argument.
            if count_increment:
                progress_callback(count_increment)
        except Exception:
            pass  # progress UI must never break a scan

    def flush() -> None:
        if not chunk:
            return
        # Parsing phase: update displayed item per file as we work
        # through the chunk. The user sees the path lacuna is
        # currently chewing on — if a slow parse hangs, the display
        # sticks on that file (the actual stuck one), not a stale
        # path from before the chunk started.
        if should_parallelize(len(chunk), jobs):
            worker_args = [
                (rel, content, ex.language_name)
                for rel, content, _h, ex in chunk
            ]
            results: dict[str, list] = {}
            # Iterate the result iterator so we can update progress as
            # each worker hands back its results. Best we can do without
            # cross-process IPC: the displayed file is the one whose
            # results just arrived, not necessarily the one currently
            # in-flight in another worker. True real-time per-worker
            # visibility needs the multi-job-viz IPC plumbing.
            for rel, items in get_pool().map(
                parse_one, worker_args, chunksize=4,
            ):
                results[rel] = items
                _emit_progress(rel, count_increment=0)
        else:
            # Serial path — update display BEFORE each parse so the
            # display reflects the file currently being processed,
            # not the one just finished.
            results = {}
            for rel, content, _h, ex in chunk:
                _emit_progress(rel, count_increment=0)
                tree_root = ex.parse(content)
                results[rel] = list(ex.extract(tree_root, rel))

        # Storage phase: this is when "work for that file" is truly
        # done, so this is where we increment the count.
        for rel, _content, current_hash, _ex in chunk:
            storage.delete_entities_for_file(rel)
            new_entities: dict[str, Entity] = {}
            new_features: dict[str, FeatureSet] = {}
            for entity, features in results.get(rel, []):
                new_entities[entity.id] = entity
                new_features[entity.id] = features
            storage.save_entities_and_features(new_entities, new_features)
            storage.upsert_file(rel, current_hash, run_id)
            _emit_progress(rel, count_increment=1)
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
                # Cache hit: the only "work" for this file is upserting
                # the cache row. Advance the bar by 1 here — work for
                # this file is genuinely complete.
                storage.upsert_file(rel, current_hash, run_id)
                files_unchanged += 1
                _emit_progress(rel, count_increment=1)
                continue

            # Changed/new file: queue it for parsing. Don't advance the
            # count yet — the parse hasn't happened. Update only the
            # displayed item so the user sees we're aware of this file.
            chunk.append((rel, content, current_hash, extractor))
            _emit_progress(rel, count_increment=0)
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
        "stage_durations_ms": scan_stats.get("stage_durations_ms"),
        "jobs": scan_stats.get("jobs"),
    }
    (state_dir / "last_run.json").write_text(
        json.dumps(summary, indent=2) + "\n"
    )

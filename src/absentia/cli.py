"""Command-line entry point.

Subcommands:
  init    create absentia.toml + .absentia/ in the current directory
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
# absentia configuration. Run `absentia check` from this directory.
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

    # Shorthand: `absentia <path>` — if the first arg isn't a known
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
        prog="absentia",
        description="Find the holes your code already drew.",
        epilog=(
            "Quick reference:\n"
            "  absentia                          open the TUI in the current directory\n"
            "  absentia PATH                     open the TUI in PATH (e.g. absentia ~/myrepo)\n"
            "  absentia init                     bootstrap a project here\n"
            "  absentia check                    batch scan, print gaps, exit non-zero on failure\n"
            "  absentia check --jobs N           override worker count (default: half of cores)\n"
            "  absentia check --max-gaps N       tolerate up to N gaps before failing (CI flag)\n"
            "  absentia est                      headline total + per-jobs check breakdown\n"
            "  absentia est --history            show recent `absentia check` runs feeding the model\n"
            "  absentia est --recalibrate        re-run calibration (also recalibrates on PATH)\n"
            "  absentia est --use-synthetic      calibrate against bundled corpus (empty cwd OK)\n"
            "  absentia suppress GAP_ID          mark a gap as intentional\n"
            "  absentia suppress --list          list current suppressions\n"
            "  absentia --jobs-default N         pin default worker count (0 = auto cpu/2)\n"
            "  absentia --purge [PATH]           delete .absentia/ from PATH (default: cwd)\n"
            "  absentia --purge-all              delete every .absentia/ under $HOME + machine cache\n"
            "\n"
            "Each subcommand has its own --help with the full flag list:\n"
            "  absentia check --help · absentia est --help · absentia suppress --help\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Carry the tagline in --version output so the wedge ("find what
    # you forgot to write") shows up at every entry-point a user
    # might type, not just on the README. Closes lacuna_doc_todos
    # §A1's last cosmetic gap (CLI banner).
    parser.add_argument(
        "--version", action="version",
        version=f"absentia {__version__} — find what you forgot to write",
    )
    parser.add_argument(
        "--purge",
        nargs="?",
        const=".",
        default=None,
        metavar="PATH",
        help="Remove absentia state (.absentia/) from PATH (default: cwd). "
             "Absentia config files (absentia.toml) are left in place.",
    )
    parser.add_argument(
        "--purge-all",
        action="store_true",
        help="Remove every absentia state directory under your home + the "
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
        help="Pin the default worker count for `absentia check` so future "
             "scans use N workers without needing `--jobs N` each time. "
             "Saved to ~/.absentia/settings.json. Pass 0 to revert to auto "
             "(half of cpu cores). If N exceeds your detected core count "
             "you'll be re-prompted, since over-subscribing usually slows "
             "the scan.",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Force-disable ANSI color in CLI output. Equivalent to "
             "setting NO_COLOR=1 in the environment; the flag wins if "
             "both are set. Useful for piping output through tools that "
             "don't strip escape sequences.",
    )
    parser.add_argument(
        "--debug", "-vv",
        action="store_true",
        help="Print extra diagnostic detail to stderr (per-stage timings, "
             "config / extractor resolution, cache decisions). Tied to "
             "dev work — opposite pole from --quiet. Doesn't change scan "
             "behavior; only what gets printed.",
    )
    parser.add_argument(
        "--info",
        action="store_true",
        help="Print a 30-second introduction to absentia (what it does, "
             "what it finds, quick-start commands, where to learn more) "
             "and exit. The same intro is one-line-hinted on first "
             "invocation in a TTY.",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=None,
        metavar="N",
        help="Worker count for the TUI's initial / re-scan. "
             "Defaults to 1 — the TUI forces single-process scans "
             "by default because spawn-mode multiprocessing can "
             "trip Textual's event loop on macOS (`bad value(s) "
             "in fds_to_keep`). Override with --jobs N if your "
             "platform handles process spawn cleanly under "
             "Textual; see the TUI keys reference for caveats. "
             "Has no effect on `absentia check` (use that "
             "subcommand's own --jobs instead).",
    )
    sub = parser.add_subparsers(dest="cmd")

    init = sub.add_parser("init", help="Create absentia.toml + .absentia/ in the current dir.")
    init.add_argument("path", nargs="?", default=".", help="Where to init (default: cwd)")
    init.add_argument("--force", action="store_true",
                      help="Overwrite an existing absentia.toml")
    init.add_argument("--quiet", "-q", action="store_true",
                      help="Suppress the 'Initialized absentia in PATH' message "
                           "and the first-scan estimate footer. Useful for "
                           "scripts that init then immediately run check.")

    check = sub.add_parser("check", help="Scan a project and print gaps.")
    check.add_argument("path", nargs="?", default=".", help="Project root (default: cwd)")
    check.add_argument("--config", type=Path, default=None,
                       help="Path to absentia.toml (default: search root upward)")
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
    check.add_argument("--max-gaps", type=int, default=None,
                       metavar="N",
                       help="Tolerance for CI: exit non-zero only when "
                            "gap count exceeds N. --max-gaps 0 fails on "
                            "any gap (the default behavior); --max-gaps 5 "
                            "lets up to 5 gaps slide. Useful for adopting "
                            "absentia on an existing codebase without "
                            "blocking the build the first day.")
    check.add_argument("--cold", nargs="?", const="", default=None,
                       metavar="PATH",
                       help="Force re-parse of files at PATH (default: "
                            "the whole scanned root). Recursive — passing a "
                            "directory cold-busts every file under it. "
                            "Tied to dev work: use when you suspect cache "
                            "weirdness, are benchmarking the parse stage, "
                            "or are validating extractor changes. Doesn't "
                            "delete the cache (next scan without --cold "
                            "is back to warm).")
    check.add_argument("--language", "--languages", default=None,
                       metavar="LANG[,LANG]",
                       help="Restrict scan to specific languages (comma-"
                            "separated). Overrides the absentia.toml "
                            "[scan.languages] list. Useful for quick "
                            "single-language re-runs ('I just edited "
                            "Python; only re-scan Python this run'). "
                            "Validates against the registered extractors.")
    check.add_argument("--exclude", action="append", default=None,
                       metavar="PATTERN",
                       help="Skip files / directories matching PATTERN "
                            "(glob, e.g. '**/vendor/**'). May be passed "
                            "multiple times. Appends to absentia.toml "
                            "[scan.exclude]; doesn't override.")

    est = sub.add_parser(
        "est",
        aliases=["estimate"],
        help=(
            "Predict total `absentia check` time without scanning. "
            "Shows a headline total ± confidence band and a per-jobs "
            "breakdown (parse + mine_tail = check). Estimates auto-"
            "improve as you run more `absentia check` invocations — "
            "every check appends to ~/.absentia/runs.jsonl and "
            "future est calls aggregate from it."
        ),
    )
    est.add_argument("path", nargs="?", default=".",
                     help="Project root (default: cwd)")
    est.add_argument("--config", type=Path, default=None,
                     help="Path to absentia.toml (default: search root upward). "
                          "Mirrors `absentia check --config`.")
    est.add_argument("--jobs", "-j", type=int, default=None, metavar="N",
                     help="Headline-estimate worker count. Overrides the "
                          "default (half of CPU cores). The full per-jobs "
                          "table still renders; this only changes which "
                          "row is highlighted as the headline number.")
    est.add_argument("--json", action="store_true", dest="as_json",
                     help="Emit machine-readable JSON instead of the human "
                          "estimate report. Use case: a CI step that decides "
                          "whether to skip a long scan based on the cost "
                          "prediction.")
    est.add_argument("--quiet", "-q", action="store_true",
                     help="Suppress the human-formatted intro / calibration "
                          "prompts and just emit the bottom-line estimate. "
                          "Implies non-interactive (no calibration prompts).")
    est.add_argument("--recalibrate", action="store_true",
                     help="Force re-running the calibration even if a "
                          "fresh cache exists.")
    est.add_argument("--use-synthetic", action="store_true",
                     help="Calibrate against a bundled synthetic Python "
                          "corpus instead of cwd. Useful when the current "
                          "directory is empty or too small for reliable "
                          "calibration.")
    est.add_argument("--history", action="store_true",
                     help="Print the recent `absentia check` runs that "
                          "feed the estimator and exit. Useful for "
                          "auditing what data the prediction is based "
                          "on (~/.absentia/runs.jsonl).")
    est.add_argument("--cold", nargs="?", const="", default=None,
                     metavar="PATH",
                     help="Predict the cold-scan time for PATH (default: "
                          "the whole scanned root) — bypass the warm-cache "
                          "credit that est would otherwise apply when "
                          "files have unchanged content hashes. Tied to "
                          "dev work: useful for predicting the worst-case "
                          "first-time-on-this-repo experience.")
    est.add_argument("--language", "--languages", default=None,
                     metavar="LANG[,LANG]",
                     help="Scope the prediction to specific languages "
                          "(comma-separated). Mirrors `absentia check "
                          "--language`. Overrides absentia.toml "
                          "[scan.languages].")
    est.add_argument("--exclude", action="append", default=None,
                     metavar="PATTERN",
                     help="Skip files / directories matching PATTERN "
                          "from the corpus walk used for the prediction. "
                          "May be passed multiple times. Appends to "
                          "absentia.toml [scan.exclude].")

    suppress = sub.add_parser(
        "suppress",
        help="Mark a gap as known/intentional so it stops appearing in check.",
    )
    suppress.add_argument("gap_id", nargs="?", default=None,
                          help="Short ('g-7c91234') or full gap id from "
                               "`absentia check` output")
    suppress.add_argument("--reason", default=None,
                          help="Required unless --list/--remove. Describes why "
                               "this gap is intentional.")
    suppress.add_argument("--remove", action="store_true",
                          help="Remove an existing suppression instead of adding one")
    suppress.add_argument("--list", action="store_true", dest="as_list",
                          help="List current suppressions and exit")
    # Project root: prefer positional `path` for symmetry with the
    # other subcommands. `--path` is kept as a deprecated alias so
    # existing scripts keep working; either form resolves to the
    # same effective root, with positional winning if both are given.
    suppress.add_argument("project_path", nargs="?", default=None,
                          metavar="PATH",
                          help="Project root (default: cwd). Mirrors the "
                               "positional argument on init/check/est.")
    suppress.add_argument("--path", default=None, dest="project_path_flag",
                          help="[Deprecated] Project root. Use the positional "
                               "PATH argument instead. Kept for backward "
                               "compatibility with existing scripts.")

    report = sub.add_parser(
        "report",
        help=(
            "Compose a GitHub issue from the TUI debug log + system "
            "info. Prompts before sending so nothing leaves the "
            "machine without your OK."
        ),
    )
    report.add_argument(
        "--no-prompt", action="store_true",
        help="Skip the [y/N] prompt and go straight to issue "
             "composition. Useful when you've already decided.",
    )

    args = parser.parse_args(argv)

    # --no-color must take effect BEFORE the first import of `_color`
    # (which evaluates _USE_COLOR at module load). _color is imported
    # lazily inside scan paths, so setting NO_COLOR here is early
    # enough — but ordering matters for any future imports that
    # happen before this point.
    if getattr(args, "no_color", False):
        import os as _os
        _os.environ["NO_COLOR"] = "1"

    # --debug exposes a process-wide flag downstream code can check.
    # Stays opt-in: all existing print paths are unchanged unless
    # they explicitly consult this. Keeps blast radius tiny.
    if getattr(args, "debug", False):
        import os as _os
        _os.environ["ABSENTIA_DEBUG"] = "1"
        # Surface immediately so the user sees we got the flag.
        print("[absentia debug] verbose mode on", file=sys.stderr)

    # --info is an early exit — print the intro and stop. Skips
    # the first-run hint (the user is already getting the full
    # intro, hinting at it would be redundant).
    if args.info:
        return cmd_info()

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

    # First-run hint: a one-liner pointing at --info that fires once,
    # ever, on the first TTY invocation that reaches subcommand
    # dispatch. Skips silently on non-TTY (CI / piped) and after
    # settings.json records info_hint_shown_at.
    _first_run_hint()

    if args.cmd is None:
        # No subcommand: launch the TUI when run from a TTY, otherwise
        # fall through to printing help (so piped usage stays sane).
        # ``tui_path`` was populated above when the user passed
        # ``absentia <path>`` — otherwise default to cwd.
        if sys.stdin.isatty() and sys.stdout.isatty():
            from .tui import run_tui
            root = tui_path if tui_path is not None else Path(".").resolve()
            config = _load_config(root, None)
            return run_tui(root, config, jobs=args.jobs)
        parser.print_help()
        return 0

    if args.cmd == "init":
        return cmd_init(root=Path(args.path).resolve(), force=args.force,
                        quiet=args.quiet)

    if args.cmd == "check":
        root = Path(args.path).resolve()
        config = _load_config(root, args.config)
        _debug(f"resolved root = {root}")
        _debug(f"languages = {list(config.scan.languages)}")
        if config.scan.exclude:
            _debug(f"excludes (from config) = {list(config.scan.exclude)}")
        config = _apply_scope_overrides(config, args.language, args.exclude)
        if args.language is not None:
            _debug(f"--language override → languages = {list(config.scan.languages)}")
        if args.exclude:
            _debug(f"--exclude override (appended) → excludes = {list(config.scan.exclude)}")
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
            max_gaps=args.max_gaps,
            cold=_resolve_cold_arg(args.cold, root),
        )

    if args.cmd in ("est", "estimate"):
        if args.history:
            return cmd_est_history()
        return cmd_est(
            root=Path(args.path).resolve(),
            recalibrate=args.recalibrate,
            use_synthetic=args.use_synthetic,
            cold=_resolve_cold_arg(args.cold, Path(args.path).resolve()),
            config_path=args.config,
            jobs=args.jobs,
            as_json=args.as_json,
            quiet=args.quiet,
            language_filter=args.language,
            excludes=args.exclude,
        )

    if args.cmd == "suppress":
        # Positional `project_path` wins over the deprecated `--path`
        # flag; both default to None so when neither is given we fall
        # back to cwd. Surfaces a one-line deprecation hint when the
        # legacy --path is used.
        if args.project_path_flag is not None and args.project_path is None:
            stderr_console.print(
                "[dim]absentia: `suppress --path` is deprecated; use the "
                "positional path argument (`absentia suppress <gap_id> PATH`).[/]"
            )
        suppress_root = (
            args.project_path
            or args.project_path_flag
            or "."
        )
        return cmd_suppress(
            root=Path(suppress_root).resolve(),
            gap_id=args.gap_id,
            reason=args.reason,
            remove=args.remove,
            as_list=args.as_list,
        )

    if args.cmd == "report":
        return cmd_report(no_prompt=args.no_prompt)

    return 0


def _load_config(root: Path, explicit: Path | None) -> Config:
    if explicit is not None:
        return Config.from_file(explicit)
    discovered = find_config(root)
    if discovered is not None:
        return Config.from_file(discovered)
    return Config()


def _apply_scope_overrides(
    config: Config,
    languages: str | None,
    excludes: list[str] | None,
) -> Config:
    from dataclasses import replace
    """Apply --language / --exclude CLI overrides to a Config in place
    (returning a new immutable Config).

    ``languages`` is a comma-separated string ("python,rust") that
    *replaces* the config's language list — the CLI flag is the user's
    explicit intent for this run and should not be merged with the
    file's default. Unknown language names are dropped silently here;
    upstream extractor discovery will produce a clearer error if the
    resulting list is empty.

    ``excludes`` is a list of glob patterns that *append* to the
    config's exclude list — the file usually carries the long-lived
    excludes (vendored deps, build artifacts) and the flag adds
    one-off exclusions for this run.
    """
    from .config import ScanConfig
    if languages is None and not excludes:
        return config
    new_languages = config.scan.languages
    if languages is not None:
        # Parse comma-separated list; strip whitespace; drop blanks.
        names = tuple(
            n.strip() for n in languages.split(",") if n.strip()
        )
        if names:
            new_languages = names
    new_exclude = config.scan.exclude
    if excludes:
        new_exclude = config.scan.exclude + tuple(excludes)
    return replace(
        config,
        scan=ScanConfig(
            include=config.scan.include,
            exclude=new_exclude,
            languages=new_languages,
        ),
    )


_INFO_LINES: tuple[str, ...] = (
    "",
    "[bold]absentia[/] — find what you forgot to write",
    "    [dim]The holes your code already drew.[/]",
    "",
    "[bold]What it does[/]",
    "  Pattern-mines your codebase and surfaces places where one piece breaks",
    "  ranks. Nine endpoints have @audit, the tenth doesn't — that's a gap.",
    "",
    "  No LLM. No rule files. Deterministic. Same input → same gaps.",
    "",
    "[bold]What it finds[/]",
    "  • Decorator inconsistency  — N of M functions in src/api/ have @audit",
    "  • Missing sibling tests    — N of M files in src/ have a tests/* sibling",
    "  • Inheritance gaps         — N of M classes in panels/ extend BasePanel",
    "  • Series gaps              — migrations/0001, 0002, 0004 (where's 0003?)",
    "  …plus call-pair, has_docstring, has_return_type, has_param_types, has_post_init, has_all_export, call_kwargs, entry_point_registered.",
    "",
    "[bold]Quick start[/]",
    "  [cyan]absentia init[/]           # generate absentia.toml in cwd",
    "  [cyan]absentia check[/]          # human-readable list of gaps; exit 1 if any",
    "  [cyan]absentia check --json[/]   # machine-readable for CI",
    "  [cyan]absentia[/]                # interactive TUI (Textual)",
    "  [cyan]absentia est[/]            # cold-scan time prediction with confidence band",
    "  [cyan]absentia suppress <id>[/]  # mark a gap as known-intentional with a reason",
    "",
    "[bold]Learn more[/]",
    "  Source     [cyan]https://github.com/skbays03/absentia[/]",
    "  Tutorial   [cyan]docs/tutorial/quickstart.md[/]",
    "",
)


def cmd_info() -> int:
    """Print the 30-second introduction. Triggered by ``--info``.

    Width is fixed at 80 columns by design — narrower terminals get
    wrap; wider terminals don't get padding. Renders to stdout so
    it's pipe-able (e.g., ``absentia --info | less``).
    """
    for line in _INFO_LINES:
        stdout_console.print(line, width=80, overflow="fold")
    return 0


def _first_run_hint() -> None:
    """Print the one-liner pointing to ``--info`` on first invocation.

    Fires when ``settings.json`` has ``info_hint_shown_at = None``
    AND stdout is a TTY (skips on piped output / CI). Updates the
    field after printing so the hint shows once, ever — even if the
    user never actually runs ``absentia --info``.

    Goes to stderr via ``stderr_console`` so it doesn't pollute
    stdout pipes when only stdout is piped.
    """
    if not sys.stdout.isatty():
        return
    from .settings import load_settings, save_settings
    s = load_settings()
    if s.info_hint_shown_at is not None:
        return
    stderr_console.print(
        "[dim]Tip: run [/dim][cyan]`absentia --info`[/cyan]"
        "[dim] for a 30-second introduction.[/dim]",
    )
    from dataclasses import replace as _replace
    from datetime import datetime, timezone
    save_settings(_replace(
        s,
        info_hint_shown_at=datetime.now(timezone.utc).isoformat(),
    ))


def cmd_init(*, root: Path, force: bool, quiet: bool = False) -> int:
    if not root.is_dir():
        stderr_console.print(f"[red]absentia:[/] not a directory: [cyan]{root}[/]")
        return 2

    config_path = root / "absentia.toml"
    state_dir = root / ".absentia"

    if config_path.exists() and not force:
        stderr_console.print(
            f"[red]absentia:[/] [cyan]{config_path}[/] already exists. "
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
        if ".absentia/" not in existing_lines and ".absentia" not in existing_lines:
            with gitignore.open("a") as fh:
                if existing_lines and existing_lines[-1] != "":
                    fh.write("\n")
                fh.write(".absentia/\n")

    if quiet:
        return 0

    stdout_console.print(
        f"[bright_green]✓[/] Initialized absentia in [cyan]{root}[/]"
    )
    stdout_console.print("  - Wrote [cyan]absentia.toml[/]")
    stdout_console.print("  - Created [cyan].absentia/[/] [dim](gitignored)[/]")
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

    stdout_console.print("Run [bold cyan]`absentia check`[/] to start exploring.")
    return 0


def _debug(msg: str) -> None:
    """Emit a diagnostic line to stderr when ABSENTIA_DEBUG is set
    (via --debug / -vv on the CLI). Cheap no-op otherwise — used
    sparingly at decision points where users investigating odd
    behavior would want to know what was chosen."""
    import os as _os
    if _os.environ.get("ABSENTIA_DEBUG"):
        print(f"[absentia debug] {msg}", file=sys.stderr)


def _resolve_cold_arg(cold_arg: str | None, fallback_root: Path) -> Path | None:
    """Translate the raw ``--cold [PATH]`` arg into an absolute Path.

    argparse with ``nargs='?'`` gives us:
      - ``None``                         (flag absent)         → return None
      - ``""`` (the const for bare flag) (--cold w/ no value)  → fallback_root
      - any string (a value was given)                          → resolve it
    """
    if cold_arg is None:
        return None
    if cold_arg == "":
        return fallback_root
    return Path(cold_arg).expanduser().resolve()


def cmd_check(
    *,
    root: Path,
    config: Config,
    quiet: bool = False,
    as_json: bool = False,
    jobs: int | None = None,
    max_gaps: int | None = None,
    cold: Path | None = None,
) -> int:
    if not root.is_dir():
        if as_json:
            print(json.dumps({"error": f"not a directory: {root}"}))
        else:
            stderr_console.print(f"[red]absentia:[/] not a directory: [cyan]{root}[/]")
        return 2

    from datetime import datetime, timezone
    started = time.perf_counter()
    started_iso = datetime.now(timezone.utc).isoformat()
    state_dir = root / ".absentia"

    try:
        lock_ctx = StateLock(state_dir / "lockfile").__enter__()
    except StateLockError as exc:
        if as_json:
            print(json.dumps({"error": str(exc)}))
        else:
            stderr_console.print(f"[red]absentia:[/] {exc}")
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
            max_gaps=max_gaps,
            cold=cold,
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
    max_gaps: int | None = None,
    cold: Path | None = None,
) -> int:
    extractors = discover_extractors(config.scan.languages)
    if not extractors:
        msg = (
            f"no extractors available for languages={list(config.scan.languages)}"
        )
        if as_json:
            print(json.dumps({"error": msg}))
        else:
            stderr_console.print(f"[red]absentia:[/] {msg}")
        return 2

    # One-line estimate preamble for interactive text mode.
    # Suppressed in JSON, quiet, and non-TTY contexts to keep
    # CI logs and machine-readable output clean. Gated on stderr
    # (where the line lands) — that way `absentia check | grep ...`
    # still surfaces the preamble for the human watching the terminal.
    interactive_text_mode = (
        not as_json and not quiet and sys.stderr.isatty()
    )

    # Walk-once cache: in interactive mode both the estimate preamble
    # and scan_corpus's parse-bar setup want a CorpusShape. Walking
    # the kernel takes ~1.5 s; doing it twice was a clean ~3 s of
    # wasted wall-clock. Walk once here, share both. Optimization
    # plan item 4.
    cached_shape: Any = None
    if interactive_text_mode:
        from .estimator import walk_corpus
        ext_to = extension_dispatch(extractors)
        try:
            cached_shape = walk_corpus(root, ext_to)
        except Exception:
            cached_shape = None  # fall through to per-callee walks

        from .estimator import quick_estimate_line
        line = quick_estimate_line(
            root=root, config=config, jobs=jobs, shape=cached_shape,
        )
        if line is not None:
            print(line, file=sys.stderr)

    result = scan_corpus(
        root=root, state_dir=state_dir, config=config,
        started=started, started_iso=started_iso, extractors=extractors,
        jobs=jobs, interactive=interactive_text_mode,
        shape=cached_shape, cold=cold,
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

        # Post-check export prompt — interactive text mode only.
        # Skipped on --json (machine output), --quiet (footer
        # suppressed), and any non-TTY context (CI, piped). Failures
        # surface inside prompt_and_export; we ignore the return
        # value because the prompt is advisory, not load-bearing.
        if (
            not quiet
            and sys.stdin.isatty()
            and sys.stdout.isatty()
        ):
            from .export import prompt_and_export
            prompt_and_export(
                root=root,
                gaps=gaps,
                rules_by_id=rules_by_id,
                entities=entities,
                scan_stats=scan_stats,
            )

    # Exit policy: --max-gaps N tolerates up to N gaps before failing
    # the build. Default (None) keeps the original "any gap fails"
    # behavior, which matches what users expect from a strict check.
    if max_gaps is None:
        return 1 if gaps else 0
    return 1 if len(gaps) > max_gaps else 0


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
    stage_callback: Any = None,
    interactive: bool = False,
    shape: Any = None,
    cold: Path | None = None,
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

    ``stage_callback``, if provided, is called as
    ``stage_callback(stage, event, **details)`` where ``stage`` is one
    of ``"walk" | "parse" | "store" | "mine" | "finalize"`` and
    ``event`` is ``"started"`` or ``"finished"``. ``details`` carries
    stage-specific kwargs on the ``"finished"`` events: ``files`` /
    ``bytes_`` for walk, ``entities`` for store, etc. Lets a non-
    interactive caller (the TUI) mirror the per-stage progress
    display the CLI shows, without piping through the stderr-bound
    Spinner / ProgressBar widgets. Skipped when ``stage_callback``
    is None. All exceptions in the callback are swallowed so a
    buggy widget can never break the scan itself.

    ``interactive`` controls per-stage TTY progress UI. When True (set
    by ``cmd_check`` in interactive text mode), each pipeline stage
    (walk, parse, store, mine, finalize) gets its own indicator that
    finishes with a ✓ summary line + elapsed time — so the user can
    see which stage just took N seconds and a hang is immediately
    diagnosable. The TUI passes ``interactive=False`` and drives its
    own widgets via ``progress_callback`` + ``stage_callback``.
    """
    def _stage(name: str, event: str, **details: Any) -> None:
        """Forward a stage transition to the optional callback,
        swallowing exceptions so a buggy caller widget can never
        break the scan."""
        if stage_callback is None:
            return
        try:
            stage_callback(name, event, **details)
        except Exception:
            pass

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
    # spinners. Persisted into last_run.json so `absentia est` can show a
    # real breakdown (parse N s + mine M s + finalize K s = total) and
    # predict full check time, not just the parse stage.
    stage_durations: dict[str, float] = {
        "walk": 0.0, "parse": 0.0, "store": 0.0,
        "mine": 0.0, "finalize": 0.0,
    }

    # ── Walk stage: count files up-front so the parse bar has a
    # total. Skipped when the caller is driving its own progress UI
    # (TUI) — unless the TUI passed a stage_callback, in which case
    # we walk anyway so the loading panel has a real file count to
    # show. Even at 65 k files this is sub-second on Mac and ~1–2 s
    # on the kernel; the spinner makes that wait visible.
    #
    # If the caller already walked the corpus (cmd_check shares one
    # walk between its estimate-preamble and this stage), we use
    # their result instead of walking again — on the kernel that
    # halves a ~3 s combined cost.
    _stage("walk", "started")
    parse_bar = None
    if interactive:
        from .estimator import _format_size, walk_corpus
        from .progress import ProgressBar, Spinner, _format_time, spinning

        if shape is None:
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
        else:
            # Pre-walked by the caller; emit the same ✓ summary line
            # so the per-stage display still has a Walk row, marked
            # as cached so the user knows we didn't re-walk.
            cached_walk = Spinner(label="Walking corpus")
            cached_walk.finish(
                end_message=(
                    f"Walked corpus  ·  {shape.files:,d} files, "
                    f"{_format_size(shape.bytes)}  ·  cached"
                )
            )

        if shape.files > 0:
            parse_bar = ProgressBar(total=shape.files, label="Scanning")
            progress_callback = parse_bar.update
    elif stage_callback is not None and shape is None:
        # Non-interactive caller (TUI) wants per-stage progress —
        # do an explicit walk so the loading panel knows the file
        # count up front. Skipped when the caller already pre-walked.
        from .estimator import walk_corpus
        walk_started = time.perf_counter()
        shape = walk_corpus(root, ext_to_extractor)
        stage_durations["walk"] = time.perf_counter() - walk_started

    walk_files = shape.files if shape is not None else 0
    walk_bytes = shape.bytes if shape is not None else 0
    _stage(
        "walk", "finished",
        files=walk_files, bytes_=walk_bytes,
        duration_ms=stage_durations["walk"] * 1000,
    )

    # Multi-worker progress UI: when interactive + jobs > 1, spin up
    # a multiprocessing.Manager() Queue + a daemon thread that drains
    # worker reports and feeds them to parse_bar.set_workers(). Workers
    # push (worker_id, language, path) before each parse so the user
    # sees one sub-line per worker, each showing the file currently in
    # flight on that worker. Skipped on serial / non-TTY paths.
    worker_report_queue: Any = None
    drain_stop: Any = None
    drain_thread: Any = None
    mp_manager: Any = None
    if interactive and parse_bar is not None and jobs > 1:
        import multiprocessing
        import threading
        try:
            mp_manager = multiprocessing.Manager()
            worker_report_queue = mp_manager.Queue()
        except Exception:
            # Manager startup can fail in exotic environments
            # (sandboxes, restricted forks). Gracefully degrade to
            # single sub-line mode.
            worker_report_queue = None

        if worker_report_queue is not None:
            active_workers: dict[str, tuple[str, str]] = {}
            drain_stop = threading.Event()

            def _drain() -> None:
                while not drain_stop.is_set():
                    try:
                        worker_id, lang, rel = worker_report_queue.get(
                            timeout=0.05,
                        )
                        active_workers[worker_id] = (lang, rel)
                        # Hand the latest snapshot to the bar; it has its
                        # own throttle so back-to-back updates are cheap.
                        parse_bar.set_workers([
                            (wid, sec, item)
                            for wid, (sec, item) in sorted(active_workers.items())
                        ])
                        parse_bar.refresh()
                    except Exception:
                        pass  # timeout or shutdown — loop and re-check

            drain_thread = threading.Thread(target=_drain, daemon=True)
            drain_thread.start()

    with Storage(state_dir) as storage:
        run_id = storage.begin_run()
        _stage("parse", "started", total=walk_files)
        parse_started = time.perf_counter()
        try:
            files_seen, files_unchanged, by_language_bytes = _scan_incremental(
                root, storage, run_id, ext_to_extractor, jobs=jobs,
                progress_callback=progress_callback,
                worker_report_queue=worker_report_queue,
                cold=cold,
                excludes=tuple(config.scan.exclude),
            )
        finally:
            # Stop the drain thread before tearing down the bar so the
            # final set_workers([]) lands cleanly.
            if drain_stop is not None:
                drain_stop.set()
            if drain_thread is not None:
                drain_thread.join(timeout=0.5)
            if parse_bar is not None:
                parse_bar.set_workers([])
                parse_bar.finish()
            if mp_manager is not None:
                try:
                    mp_manager.shutdown()
                except Exception:
                    pass
        stage_durations["parse"] = time.perf_counter() - parse_started
        _stage(
            "parse", "finished",
            files=files_seen, unchanged=files_unchanged,
            duration_ms=stage_durations["parse"] * 1000,
        )

        # ── Storage-commit stage ──────────────────────────────────
        _stage("store", "started")
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
        _stage(
            "store", "finished",
            entities=len(entities),
            duration_ms=stage_durations["store"] * 1000,
        )

        # Corpus-level feature enrichment: features that need to know
        # about the whole corpus (e.g. sibling_test, which checks
        # whether a matching test entity exists). Runs in memory only;
        # not persisted because the result depends on the full set of
        # entities, not on any single file.
        from .enrichment import enrich_all
        enrich_all(entities, feature_index, root=root)

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
        from .closure import find_unused_class_gaps
        from .series import (
            find_letter_series_gaps,
            find_ordinal_series_gaps,
            find_series_gaps,
            find_version_directory_gaps,
        )

        rules: list = []
        gaps: list = []

        def _mine_kind(kind: str, hook: Any) -> tuple[list, list]:
            rs, gs = mine(
                groups, feature_index,
                min_confidence=config.mining.min_confidence,
                feature_kind=kind,
                progress_hook=hook,
            )
            return list(rs), list(gs)

        # Worker cap depends on whether we have a GIL: 4 on regular
        # CPython (Amdahl's `p` plateaus there under GIL contention),
        # 7 on a free-threaded build (one per mining strategy → real
        # parallelism). See parallel.mining_worker_cap.
        from .parallel import mining_worker_cap
        mining_workers = mining_worker_cap(jobs)

        # The mining tasks; a list of (label, callable) we'll submit
        # as one batch. Each callable accepts a label-bound progress
        # hook (built by _make_hook in _timed) so the strategy can
        # report phase / counter / current_item back to the spinner.
        mining_tasks: list[tuple[str, Any]] = [
            ("frequency:decorator",
                lambda h: _mine_kind("decorator", h)),
            ("frequency:calls",
                lambda h: _mine_kind("calls", h)),
            ("frequency:parent_class",
                lambda h: _mine_kind("parent_class", h)),
            ("frequency:sibling_test",
                lambda h: _mine_kind("sibling_test", h)),
            ("frequency:has_docstring",
                lambda h: _mine_kind("has_docstring", h)),
            ("frequency:has_return_type",
                lambda h: _mine_kind("has_return_type", h)),
            ("frequency:has_param_types",
                lambda h: _mine_kind("has_param_types", h)),
            ("frequency:has_post_init",
                lambda h: _mine_kind("has_post_init", h)),
            ("frequency:has_all_export",
                lambda h: _mine_kind("has_all_export", h)),
            ("frequency:call_kwargs",
                lambda h: _mine_kind("call_kwargs", h)),
            ("frequency:entry_point_registered",
                lambda h: _mine_kind("entry_point_registered", h)),
            ("symmetry pairs",
                lambda h: find_symmetry_gaps(entities, progress_hook=h)),
            ("call-pair",
                lambda h: find_call_pair_gaps(entities, feature_index, progress_hook=h)),
            ("series",
                lambda h: find_series_gaps(entities, progress_hook=h)),
            ("letter-series",
                lambda h: find_letter_series_gaps(entities, progress_hook=h)),
            ("version-dir-series",
                lambda h: find_version_directory_gaps(entities, progress_hook=h)),
            ("ordinal-series",
                lambda h: find_ordinal_series_gaps(entities, progress_hook=h)),
            ("closure",
                lambda h: find_unused_class_gaps(
                    entities, feature_index, root=root, progress_hook=h,
                )),
        ]

        # Per-strategy timing. With ThreadPool + GIL the wall-clock per
        # task is *interleaved* serial time, not pure-CPU time — but
        # the relative ordering still identifies the long pole, which
        # is what we use this for (target picker for algorithmic
        # tuning + visible in `absentia est --history`).
        mine_strategy_durations: dict[str, float] = {}

        # Active-strategy registry for the multi-worker spinner view.
        # Each strategy's _timed wrapper drops a StrategyState here on
        # entry, removes it on exit. Each strategy also receives a
        # label-bound `progress_hook(phase=..., counter=..., item=...)`
        # that updates the same StrategyState — throttled to 20 Hz so
        # tight inner loops can call it freely without measurable cost.
        # The spinner daemon reads the dict snapshot every 100 ms.
        import threading as _threading
        from dataclasses import dataclass, field

        @dataclass
        class StrategyState:
            started: float
            phase: str = ""
            counter: tuple[int, int] = (0, 0)  # (current, total); 0,0 = unknown
            current_item: str = ""
            _last_hook: float = field(default=0.0, compare=False)

        _active_strategies: dict[str, StrategyState] = {}
        _active_lock = _threading.Lock()

        # 50 ms throttle inside the hook caps inner-loop overhead at
        # ~20 calls/sec/strategy regardless of how often each strategy
        # invokes the hook. The user can't perceive faster updates and
        # this keeps `find_call_pair_gaps`-style hot loops free of
        # measurable cost. See the perf analysis around this commit.
        _HOOK_THROTTLE_S = 0.05

        def _make_hook(label: str) -> Any:
            """Build a label-bound hook for one strategy. Captures the
            label so each strategy can call ``hook(...)`` without
            knowing its own name. ``item`` accepts a callable so the
            f-string only runs after we decide to accept the update."""
            def hook(
                *,
                phase: str | None = None,
                counter: tuple[int, int] | None = None,
                item: Any = None,
            ) -> None:
                now = time.perf_counter()
                state = _active_strategies.get(label)
                if state is None:
                    return
                if now - state._last_hook < _HOOK_THROTTLE_S:
                    return
                state._last_hook = now
                if phase is not None:
                    state.phase = phase
                if counter is not None:
                    state.counter = counter
                if item is not None:
                    state.current_item = str(item() if callable(item) else item)
            return hook

        def _timed(label: str, fn: Any) -> tuple[str, float, list, list]:
            with _active_lock:
                _active_strategies[label] = StrategyState(started=time.perf_counter())
            try:
                t0 = time.perf_counter()
                rs, gs = fn(_make_hook(label))
                return label, time.perf_counter() - t0, rs, gs
            finally:
                with _active_lock:
                    _active_strategies.pop(label, None)

        _stage("mine", "started", strategies=len(mining_tasks))
        mine_started = time.perf_counter()
        if interactive:
            mine_spinner = Spinner(label="Mining rules")
            done = 0
            total_tasks = len(mining_tasks)

            # Daemon thread refreshes the spinner's worker list on a
            # short cadence so the user sees strategies appear when
            # they actually start running and disappear when they
            # finish — independent of the `as_completed` loop below
            # which only fires on completion events.
            _ws_stop = _threading.Event()

            def _format_counter(c: tuple[int, int]) -> str:
                cur, total = c
                if total <= 0:
                    return ""
                # Compact for large numbers: "12k/65k" instead of full digits.
                def _h(n: int) -> str:
                    if n >= 1_000_000:
                        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
                    if n >= 1_000:
                        return f"{n / 1_000:.0f}k"
                    return f"{n:,d}"
                return f"{_h(cur)}/{_h(total)}"

            def _workers_loop() -> None:
                while not _ws_stop.wait(0.1):
                    now = time.perf_counter()
                    with _active_lock:
                        snapshot = sorted(_active_strategies.items())
                    rows: list[tuple[str, str, str]] = []
                    for lbl, st in snapshot:
                        elapsed = _format_time(now - st.started)
                        bits = [elapsed]
                        if st.phase:
                            bits.append(f"[{st.phase}]")
                        c_str = _format_counter(st.counter)
                        if c_str:
                            bits.append(c_str)
                        if st.current_item:
                            bits.append(st.current_item)
                        # Pack the full strategy detail into the
                        # `item` slot of the (worker, section, item)
                        # tuple — empty `section` so the language-tag
                        # bracket is omitted (it would be redundant
                        # with the strategy label here).
                        rows.append((lbl, "", "  ".join(bits)))
                    mine_spinner.set_workers(rows)

            _ws_thread = _threading.Thread(target=_workers_loop, daemon=True)
            _ws_thread.start()

            with spinning(mine_spinner), \
                    ThreadPoolExecutor(max_workers=mining_workers) as ex:
                fut_to_label = {
                    ex.submit(_timed, label, fn): label
                    for label, fn in mining_tasks
                }
                from concurrent.futures import as_completed
                for fut in as_completed(fut_to_label):
                    label, dur, rs, gs = fut.result()
                    mine_strategy_durations[label] = dur
                    rules.extend(rs)
                    gaps.extend(gs)
                    done += 1
                    # Top-line status still tracks done count; the
                    # workers list (set by _workers_loop) carries the
                    # per-strategy detail.
                    mine_spinner.set_current_item(
                        f"{done}/{total_tasks} done · "
                        f"{len(rules):,d} rules so far"
                    )

            _ws_stop.set()
            _ws_thread.join(timeout=0.5)
            mine_spinner.set_workers([])
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
                futures = [
                    ex.submit(_timed, label, fn)
                    for label, fn in mining_tasks
                ]
                for fut in futures:
                    label, dur, rs, gs = fut.result()
                    mine_strategy_durations[label] = dur
                    rules.extend(rs)
                    gaps.extend(gs)
            stage_durations["mine"] = time.perf_counter() - mine_started
        _stage(
            "mine", "finished",
            rules=len(rules), gaps=len(gaps),
            duration_ms=stage_durations["mine"] * 1000,
        )

        # ── Finalize stage: dedup, suppress, end_run ──────────────
        _stage("finalize", "started")
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
        # Project-wide suppressions from absentia.toml's
        # [[suppress]] blocks. Local DB + project entries are
        # AND'd into the same filter — both shed gaps from the
        # final list. See _suppressions.py for scope semantics.
        from ._suppressions import (
            gap_matches_project_entry,
            load_project_suppressions,
        )
        project_entries = load_project_suppressions(root)
        suppressed_count = 0
        suppressed_by_project = 0
        if (
            suppressed_short_ids or suppressed_full_ids
            or project_entries
        ):
            kept = []
            for gap in gaps:
                if (
                    gap.short_id in suppressed_short_ids
                    or gap.id in suppressed_full_ids
                ):
                    suppressed_count += 1
                    continue
                # Project filter — only relevant when a TOML
                # entry exists, but the predicate is cheap so
                # the unconditional walk is fine.
                rule = next(
                    (r for r in rules if r.id == gap.rule_id), None,
                )
                rule_value = rule.feature_value if rule else ""
                if any(
                    gap_matches_project_entry(
                        entity_id=gap.entity_id,
                        rule_id=gap.rule_id,
                        rule_feature_value=rule_value,
                        entry=entry,
                    )
                    for entry in project_entries
                ):
                    suppressed_count += 1
                    suppressed_by_project += 1
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
        _stage(
            "finalize", "finished",
            gaps=len(gaps), suppressed=suppressed_count,
            duration_ms=stage_durations["finalize"] * 1000,
        )
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
        "mine_by_strategy_ms": {
            label: round(secs * 1000, 2)
            for label, secs in mine_strategy_durations.items()
        },
        "jobs": jobs,
        "by_language_bytes": by_language_bytes,
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
    """Remove absentia state from a single project root.

    Only removes ``.absentia/`` (the gitignored state directory).
    Leaves ``absentia.toml`` in place — that's a versioned config the
    user might want to keep for re-running scans later.

    When ``confirm`` is True (the default), prompts ``[y/N]`` with a
    disclaimer of what's about to be deleted. Refuses outright in
    non-interactive contexts unless ``confirm=False`` (set by
    ``--yes``/``-y``).
    """
    import shutil

    if not root.is_dir():
        stderr_console.print(f"[red]absentia:[/] not a directory: [cyan]{root}[/]")
        return 2

    target = root / ".absentia"
    if not target.exists():
        stdout_console.print(
            f"[dim]absentia: no .absentia/ directory at [cyan]{root}[/]; "
            f"nothing to purge.[/]"
        )
        return 0
    if not target.is_dir():
        stderr_console.print(
            f"[red]absentia:[/] [cyan]{target}[/] exists but isn't a directory; "
            f"refusing to remove."
        )
        return 1

    # Sanity check: verify it looks like a absentia state dir before deleting.
    # A real  absentia .absentia/ has at least a `version` file and `state.db`.
    looks_absentia = (
        (target / "version").exists() or (target / "state.db").exists()
    )
    if not looks_absentia:
        stderr_console.print(
            f"[red]absentia:[/] [cyan]{target}[/] doesn't look like an absentia "
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
        print("  - suppressions you've added with `absentia suppress`")
        print("  - last_run.json — timing/stats from the previous scan")
        print()
        print("Your source code and absentia.toml are NOT touched. The next")
        print("scan will be a full cold scan instead of incremental.")
        print()
        if not sys.stdin.isatty():
            print(
                "absentia --purge: refusing to delete in non-interactive "
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
    if (root / "absentia.toml").exists():
        print(
            f"  (absentia.toml at {root} kept; "
            f"delete manually if you also want to remove config)"
        )
    return 0


def cmd_purge_all(*, confirm: bool = True) -> int:
    """Remove every absentia state dir under $HOME + the machine-wide cache.

    Confirms before deleting; prints what will be removed first so the
    user can sanity-check. ``confirm=False`` (set by ``--yes``/``-y``)
    skips the prompt — use only in scripted contexts.
    """
    import shutil

    home = Path.home()

    # Skip dirs that almost certainly aren't going to host a absentia state
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
    machine_cache = home / ".absentia"

    # rglob over $HOME can be 10–60 s on a packed home dir; use a
    # spinner so the user sees the tool is alive.
    from .progress import Spinner, spinning
    spinner = Spinner(label=f"Scanning {home} for absentia state")
    try:
        with spinning(spinner):
            for path in home.rglob(".absentia"):
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
        stderr_console.print(f"[red]absentia:[/] scan failed: {e}")
        return 2
    spinner.finish(end_message=f"Scan complete ({len(project_state_dirs)} project state dirs found)")

    # Machine-wide cache (different shape — has calibration.json, no version)
    machine_cache_present = (
        machine_cache.is_dir()
        and (machine_cache / "calibration.json").exists()
    )

    if not project_state_dirs and not machine_cache_present:
        stdout_console.print("[dim]No absentia state found.[/]")
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
    print("    `absentia est` will re-prompt for first-run calibration.")
    print()
    print("Source code, absentia.toml configs, and the  absentia binary itself")
    print("are NOT touched.")
    print()

    if confirm:
        if not sys.stdin.isatty():
            print(
                "absentia --purge-all: refusing to delete in non-interactive "
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
    """Pin the default worker count to ``~/.absentia/settings.json``.

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
            f"[red]absentia:[/] --jobs-default must be ≥ 0, got [bold]{n}[/]."
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
                    f"[red]absentia:[/] non-interactive context; refusing to "
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
                        f"[red]absentia:[/] not a number: {response!r}"
                    )
                    return 2
            else:
                alt = int(response)
                if alt < 1:
                    stderr_console.print(
                        f"[red]absentia:[/] jobs must be ≥ 1, got {alt}."
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


def cmd_est_history() -> int:
    """Print the recent ``absentia check`` runs that feed the estimator.

    Sourced from ``~/.absentia/runs.jsonl``. Useful for seeing what data
    the predictions are based on — e.g. "I keep getting 'low
    confidence' on a Rust corpus" → check whether prior runs covered
    Rust at all.
    """
    from .calibration import detect_cores
    from .runs_log import (
        aggregate, load_recent_runs, runs_log_path,
    )

    log_path = runs_log_path()
    runs = load_recent_runs()
    if not runs:
        stdout_console.print(
            f"[dim]No prior runs at [cyan]{log_path}[/]. Run "
            f"[bold cyan]`absentia check`[/] in any project to start "
            f"populating the log.[/]"
        )
        return 0

    aggregated = aggregate(
        runs,
        current_cores=detect_cores(),
        current_version=__version__,
    )

    stdout_console.print(
        f"[bold]Recent runs[/] from [cyan]{log_path}[/] "
        f"([bold]{len(runs)}[/] total, "
        f"[bold]{aggregated.runs_used}[/] compatible with this "
        f"machine, [dim]{aggregated.runs_skipped} skipped[/])"
    )
    stdout_console.print("")
    stdout_console.print(
        f"  {'when':<20s}  {'jobs':>4s}  {'files':>7s}  "
        f"{'check':>8s}  {'parse':>8s}  {'mine':>8s}  root"
    )
    for r in runs[-30:]:  # most recent 30
        ts = (r.get("ts") or "")[:19].replace("T", " ")
        jobs = r.get("jobs") or "?"
        files = r.get("files") or 0
        stage = r.get("stage_ms") or {}
        parse_s = float(stage.get("parse") or 0) / 1000.0
        mine_s = (
            float(stage.get("mine") or 0)
            + float(stage.get("finalize") or 0)
        ) / 1000.0
        check_s = sum(
            float(v or 0) for v in stage.values()
        ) / 1000.0
        root = r.get("root") or "?"
        if len(root) > 50:
            root = "..." + root[-47:]
        stdout_console.print(
            f"  [dim]{ts:<20s}[/]  {jobs:>4}  {files:>7,d}  "
            f"{check_s:>7.1f}s  {parse_s:>7.1f}s  {mine_s:>7.1f}s  "
            f"[cyan]{root}[/]"
        )
    if aggregated.mining_seconds_per_byte is not None:
        stdout_console.print("")
        stdout_console.print(
            f"  [bold]Aggregated mining throughput:[/] "
            f"[bright_green]{aggregated.mining_seconds_per_byte * 1e9:.1f} "
            f"ns/byte[/] across [bold]{aggregated.runs_used}[/] runs."
        )
    elif aggregated.runs_used < 3:
        stdout_console.print("")
        stdout_console.print(
            f"  [dim](need ≥ 3 compatible runs to aggregate; have "
            f"{aggregated.runs_used})[/]"
        )
    return 0


def cmd_est(
    *, root: Path, recalibrate: bool, use_synthetic: bool = False,
    cold: Path | None = None,
    config_path: Path | None = None,
    jobs: int | None = None,
    as_json: bool = False,
    quiet: bool = False,
    language_filter: str | None = None,
    excludes: list[str] | None = None,
) -> int:
    """Estimate cold-scan time without actually scanning.

    Walks the corpus, applies the cost model (calibrated if available,
    M-series baseline otherwise), prints the jobs-vs-time table.

    First-run flow: if no calibration cache exists at
    ``~/.absentia/calibration.json`` and we're attached to a TTY, prompt
    the user once to run calibration. Result is cached forever (until
    invalidated by version/core-count change or ``--recalibrate``).

    ``cold``, if provided, scopes the walk + prediction to PATH (a
    subtree of root, or a single file). est is always a cold-scan
    prediction — the flag exists for symmetry with ``check --cold``
    and to override warm-rescan-aware predictions if est ever gains
    them. Until then this is functionally identical to passing PATH
    as the positional ``root`` argument; the symmetry helps muscle
    memory and keeps the help text consistent across subcommands.
    """
    if cold is not None:
        root = cold
    from .calibration import (
        calibrated_bps_table,
        calibration_path,
        detect_cores,
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
        if as_json:
            print(json.dumps({"error": f"not a directory: {root}"}))
        else:
            stderr_console.print(f"[red]absentia:[/] not a directory: [cyan]{root}[/]")
        return 2

    config = _load_config(root, config_path)
    config = _apply_scope_overrides(config, language_filter, excludes)
    extractors = discover_extractors(config.scan.languages)
    if not extractors:
        msg = (
            f"no extractors available for languages={list(config.scan.languages)}"
        )
        print(f"absentia: {msg}", file=sys.stderr)
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
            excludes=tuple(config.scan.exclude),
        )
    spinner.finish()

    # When --use-synthetic is requested we calibrate on a bundled
    # synthetic corpus regardless of whether `root` has source files.
    # An empty root just means "no estimate report at the end."
    if shape.files == 0 and not use_synthetic:
        print(
            f"absentia: found no source files under {root} for "
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
            "`absentia est` interactively to calibrate)[/]\n"
        )

    # ── Reality check from prior scan ───────────────────────────────
    observed_cold_scan_s: float | None = None
    observed_stage_durations: dict[str, float] | None = None
    observed_jobs: int | None = None
    last_run_path = root / ".absentia" / "last_run.json"
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
    # observed last_run.json data exists). Source priority:
    #   1. Aggregated runs (~/.absentia/runs.jsonl) — if ≥3 fresh runs
    #      with this machine's cores+version exist, derive
    #      mining_seconds_per_byte from them. Beats one-shot
    #      calibration because it averages real-world variance.
    #   2. Static calibration (calibration.json) — the seed value.
    #   3. None — falls through to parse-only table.
    # When observed_stage_durations is also present, that wins inside
    # format_estimate_report — observed beats both modeled sources.
    from .runs_log import aggregate, load_recent_runs
    runs = load_recent_runs()
    aggregated = aggregate(
        runs,
        current_cores=detect_cores(),
        current_version=__version__,
    )

    model_mining_spb: float | None = None
    model_mining_source: str = ""
    if aggregated.mining_seconds_per_byte is not None:
        model_mining_spb = aggregated.mining_seconds_per_byte
        model_mining_source = (
            f"aggregated from {aggregated.runs_used} prior runs"
        )
    elif (
        calibration is not None
        and calibration.mining_seconds_per_byte > 0
    ):
        model_mining_spb = calibration.mining_seconds_per_byte
        model_mining_source = "from calibration"

    model_mining_tail_s: float | None = None
    if model_mining_spb is not None and shape.bytes > 0:
        model_mining_tail_s = shape.bytes * model_mining_spb

    # Synthetic-only case: calibration ran but root has no source files,
    # so there's nothing to estimate. Calibration result is what they
    # came for; report it and exit.
    if shape.files == 0:
        print(
            f"\nNo source files in {root} to estimate against. "
            f"Calibration is cached for future `absentia est` runs."
        )
        return 0

    # Coverage = languages with their own bps measurement OR any
    # language that appeared in the calibration corpus (those got
    # global-speed-factor scaling, which is wider-band but still
    # better than uncalibrated baseline).
    calibrated_languages: set[str] = set()
    if calibration is not None:
        calibrated_languages |= set(calibration.per_language_bps.keys())
        calibrated_languages |= set(calibration.calibration_corpus_languages)

    # --jobs override: highlight a specific worker count as the
    # headline. Falls back to the user's pinned default when None.
    headline_jobs = jobs if jobs is not None else default_jobs()

    if as_json:
        # Machine-readable estimate: minimal stable shape for CI uses.
        # Includes the headline-jobs prediction, key calibration
        # metadata, and corpus shape so downstream code can decide
        # whether to skip/run a scan based on cost.
        cpu = cpu_count_for_estimator()
        result = {
            "root": str(root),
            "files": shape.files,
            "bytes": shape.bytes,
            "cpu_count": cpu,
            "headline_jobs": headline_jobs,
            "calibrated": calibration is not None,
            "calibrated_at": (
                calibration.calibrated_at if calibration else None
            ),
            "model_mining_tail_s": model_mining_tail_s,
            "model_mining_source": model_mining_source,
            "observed_cold_scan_s": observed_cold_scan_s,
            "observed_jobs": observed_jobs,
            "runs_aggregated": aggregated.runs_used,
            "parallel_fraction": p_value,
        }
        print(json.dumps(result, indent=2))
        return 0

    report = format_estimate_report(
        root=root,
        shape=shape,
        cpu_count=cpu_count_for_estimator(),
        default_jobs=headline_jobs,
        calibrated=calibration is not None,
        calibrated_at=calibration.calibrated_at if calibration else None,
        observed_cold_scan_s=observed_cold_scan_s,
        observed_stage_durations=observed_stage_durations,
        observed_jobs=observed_jobs,
        model_mining_tail_s=model_mining_tail_s,
        model_mining_source=model_mining_source,
        runs_used=aggregated.runs_used,
        calibrated_languages=calibrated_languages,
        bps_table=bps_table,
        parallel_fraction=p_value,
    )
    if quiet:
        # --quiet: show only the bottom-line prediction line, not
        # the calibration intro / per-jobs table / methodology link.
        # The headline lives on the line starting with "Total check
        # estimate" (a stable marker in format_estimate_report).
        for line in report.splitlines():
            if line.lstrip().startswith("Total check estimate"):
                print(line.strip())
                break
        else:
            # Fallback: print the last non-blank line. Better than
            # silent if the report shape ever changes.
            for line in reversed(report.splitlines()):
                if line.strip():
                    print(line.strip())
                    break
        return 0
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
            "[cyan]~/.absentia/calibration.json[/] (only runs once)."
        )
        if not _prompt_yn("Calibrate now?", default=True):
            stdout_console.print(
                "[dim]Skipping calibration; using M-series baseline.[/]\n"
            )
            return None

    # Synthetic-corpus shortcut: skip the path-prompt loop entirely.
    if use_synthetic:
        with tempfile.TemporaryDirectory(prefix="absentia-synth-") as tmpd:
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
                f"[red]absentia:[/] not a directory: [cyan]{target}[/]"
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
    state_dir = root / ".absentia"
    if not state_dir.is_dir():
        stderr_console.print(
            f"[red]absentia:[/] no [cyan].absentia/[/] in [cyan]{root}[/]. "
            f"Run [bold cyan]`absentia check`[/] first."
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
                "[red]absentia:[/] gap_id required "
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
                "[red]absentia:[/] [bold]--reason[/] required "
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


# ── absentia report ─────────────────────────────────────────────────

_BUG_REPORT_REPO = "skbays03/absentia"
_TUI_LOG_PATH = Path.home() / ".absentia" / "tui.log"
_RUNS_LOG_PATH = Path.home() / ".absentia" / "runs.jsonl"
_BUG_REPORT_LOG_TAIL_LINES = 200
_BUG_REPORT_LOG_MAX_BYTES = 60_000


def cmd_report(*, no_prompt: bool = False) -> int:
    """Compose a GitHub bug report with the TUI debug log + system info.

    Best-effort and conservative: shows the user exactly what would be
    sent and prompts ``[y/N]`` (default no) before anything leaves the
    machine. ``--no-prompt`` skips that confirmation when the user has
    already decided. If the GitHub CLI (``gh``) is available and
    authenticated we file the issue directly; otherwise we open a
    prefilled issue URL in the user's browser.
    """
    import platform
    import shutil
    import subprocess
    import sys as _sys
    import urllib.parse
    import webbrowser

    log_excerpt = _read_tail(
        _TUI_LOG_PATH,
        max_lines=_BUG_REPORT_LOG_TAIL_LINES,
        max_bytes=_BUG_REPORT_LOG_MAX_BYTES,
    )
    last_run_excerpt = _read_tail(
        _RUNS_LOG_PATH, max_lines=3, max_bytes=4_000,
    )
    abs_version = _detect_absentia_version()

    sys_info = (
        f"- absentia {abs_version}\n"
        f"- Python {_sys.version.split()[0]}\n"
        f"- {platform.system()} {platform.release()} "
        f"({platform.machine()})\n"
    )

    title = "[bug] absentia TUI crash"
    body = (
        "## What happened\n\n"
        "_(Edit this section with what you were doing when the "
        "crash occurred.)_\n\n"
        "## System\n\n"
        f"{sys_info}\n"
        "## TUI debug log "
        f"(`~/.absentia/tui.log`, last "
        f"{_BUG_REPORT_LOG_TAIL_LINES} lines)\n\n"
        "```\n"
        f"{log_excerpt or '(no log found)'}\n"
        "```\n"
    )
    if last_run_excerpt:
        body += (
            "\n## Recent runs (`~/.absentia/runs.jsonl`)\n\n"
            "```\n"
            f"{last_run_excerpt}\n"
            "```\n"
        )

    stdout_console.print(
        "[bold]absentia report[/] — preparing a GitHub issue."
    )
    stdout_console.print(
        f"  Title: [cyan]{title}[/]"
    )
    stdout_console.print(
        f"  Log:   [cyan]{_TUI_LOG_PATH}[/]"
        + ("" if log_excerpt else " [dim](empty / not found)[/]")
    )
    stdout_console.print(
        f"  Repo:  [cyan]{_BUG_REPORT_REPO}[/]"
    )
    stdout_console.print(
        "[dim]The issue body is shown in your browser/editor before "
        "submission — nothing is sent until you confirm there too.[/]"
    )

    if not no_prompt:
        if not _sys.stdin.isatty():
            stderr_console.print(
                "[red]absentia report:[/] non-interactive shell; "
                "re-run with [bold]--no-prompt[/] if you've already "
                "decided to file."
            )
            return 2
        try:
            answer = input("File this report? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            stdout_console.print()
            return 1
        if answer not in {"y", "yes"}:
            stdout_console.print("[dim]Cancelled.[/]")
            return 0

    gh = shutil.which("gh")
    if gh is not None:
        try:
            proc = subprocess.run(
                [
                    gh, "issue", "create",
                    "--repo", _BUG_REPORT_REPO,
                    "--title", title,
                    "--body", body,
                    "--web",
                ],
                check=False,
            )
            if proc.returncode == 0:
                stdout_console.print(
                    "[bright_green]✓[/] Opened a prefilled issue via "
                    "[bold]gh[/]."
                )
                return 0
            stderr_console.print(
                f"[yellow]gh exited {proc.returncode}; falling back "
                f"to browser URL.[/]"
            )
        except OSError as exc:
            stderr_console.print(
                f"[yellow]gh failed ({exc}); falling back to browser "
                f"URL.[/]"
            )

    qs = urllib.parse.urlencode({"title": title, "body": body})
    url = f"https://github.com/{_BUG_REPORT_REPO}/issues/new?{qs}"
    if len(url) > 8_000:
        # GitHub silently truncates very long URLs. Trim the body and
        # tell the user.
        body_short = body[:6_000] + "\n\n_(log truncated for URL " \
            "length — paste the full log from " \
            f"`{_TUI_LOG_PATH}`)_\n"
        qs = urllib.parse.urlencode({"title": title, "body": body_short})
        url = f"https://github.com/{_BUG_REPORT_REPO}/issues/new?{qs}"
    stdout_console.print(
        f"Opening browser: [cyan]{url[:100]}...[/]"
        if len(url) > 100
        else f"Opening browser: [cyan]{url}[/]"
    )
    try:
        webbrowser.open(url)
    except Exception as exc:  # noqa: BLE001 — best-effort
        stderr_console.print(
            f"[red]Couldn't open browser ({exc}). Copy this URL "
            f"manually:[/]\n{url}"
        )
        return 1
    return 0


def _read_tail(
    path: Path, *, max_lines: int, max_bytes: int,
) -> str:
    """Read the last ``max_lines`` of ``path``, capped at ``max_bytes``.

    Returns ``""`` if the file is missing or unreadable.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = text.splitlines()[-max_lines:]
    out = "\n".join(lines)
    if len(out) > max_bytes:
        out = "...[truncated]...\n" + out[-max_bytes:]
    return out


def _detect_absentia_version() -> str:
    try:
        from importlib.metadata import version as _v
        return _v("absentia")
    except Exception:  # noqa: BLE001
        return "unknown"


def _scan_incremental(
    root: Path,
    storage: Storage,
    run_id: int,
    ext_to_extractor: dict,
    jobs: int = 1,
    progress_callback: Any = None,
    worker_report_queue: Any = None,
    cold: Path | None = None,
    excludes: tuple[str, ...] = (),
) -> tuple[int, int, dict[str, int]]:
    """Walk the corpus, reusing cached entities/features for unchanged files.

    Returns ``(files_seen, files_unchanged, by_language_bytes)`` for
    the run summary. The ``by_language_bytes`` map covers every file
    visited (cached + changed); it feeds the machine-wide runs log
    so `absentia est` can refine predictions across past scans.

    When ``jobs > 1`` and a chunk has enough changed files to amortize
    process startup, parse + extract runs across a worker pool. Storage
    writes always stay on the main process (SQLite is single-writer).
    The worker pool is created lazily on the first chunk that needs it,
    so projects with no changed files (or very few) pay no overhead.

    ``progress_callback``, if provided, is called with ``(n)`` after
    each batch of n files has been processed. The caller-side
    ProgressBar handles total/percent/ETA rendering.

    ``worker_report_queue``, if provided, is installed as the worker-pool
    initializer's queue: each worker pushes (worker_id, language, path)
    before processing each file so the caller can render a per-worker
    multi-line progress UI. Pass None to disable per-worker reporting.

    ``cold``, if provided, is an absolute path; any file under that path
    (or the file itself, if ``cold`` points at one file) is treated as
    a cache-miss for this run, forcing re-parse. The cache itself is
    not deleted — the next scan without ``cold`` is back to warm.
    """
    from .extractors import EXTRACTOR_FINGERPRINT
    from .parallel import init_parse_worker, parse_one, should_parallelize

    cached = storage.all_file_hashes()
    seen_paths: set[str] = set()
    files_unchanged = 0
    by_language_bytes: dict[str, int] = {}

    # Pre-compute the cold check once. ``cold`` is an absolute path on
    # disk; we compare each file's absolute path against it. Path is
    # treated as a directory prefix when it's a directory; equality
    # match when it's a file. None disables the check entirely.
    cold_resolved: Path | None = cold.resolve() if cold is not None else None
    cold_is_dir = cold_resolved is not None and cold_resolved.is_dir()

    # Cache-key salt. Bumping ``EXTRACTOR_FINGERPRINT`` invalidates every
    # cached file so the next scan re-extracts and picks up new
    # feature_kinds / entity kinds / extractor fixes — without the
    # user having to know about it. See extractors/__init__.py for
    # bump policy.
    fingerprint_salt = EXTRACTOR_FINGERPRINT.encode("utf-8")

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
            pool = ProcessPoolExecutor(
                max_workers=jobs,
                initializer=init_parse_worker,
                initargs=(worker_report_queue,),
            )
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
        # through the chunk. The user sees the path  absentia is
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
        for path in find_source_files(
            root, ext_to_extractor.keys(), excludes=excludes,
        ):
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

            # Bucket bytes by language for the runs-log aggregation.
            # We do this for every file visited (cached + changed),
            # not just changed ones, because the predictor wants the
            # *whole corpus* shape, not the dirty subset.
            by_language_bytes[extractor.language_name] = (
                by_language_bytes.get(extractor.language_name, 0)
                + len(content)
            )

            current_hash = hashlib.sha256(content + fingerprint_salt).hexdigest()

            # --cold path-scope: bypass the cache for files inside the
            # requested cold subtree (or matching the cold path exactly
            # when it's a single file).
            forced_cold = False
            if cold_resolved is not None:
                abs_path = path.resolve()
                if cold_is_dir:
                    try:
                        abs_path.relative_to(cold_resolved)
                        forced_cold = True
                    except ValueError:
                        forced_cold = False
                else:
                    forced_cold = (abs_path == cold_resolved)

            if not forced_cold and cached.get(rel) == current_hash:
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

    return len(seen_paths), files_unchanged, by_language_bytes


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

    # Append to the machine-wide runs log so `absentia est` can refine
    # its predictions across every project we've scanned. Best-effort:
    # log-append failures don't break a successful scan.
    _append_run_to_global_log(scan_stats, gaps_found)


def _append_run_to_global_log(scan_stats: dict, gaps_found: int) -> None:
    """Append a row to ~/.absentia/runs.jsonl. Best-effort; isolates any
    aggregation/IO concerns from the scan path."""
    try:
        from .calibration import detect_cores
        from .runs_log import append_run
        from . import __version__
        record = {
            "ts": scan_stats.get("started_at"),
            "version": __version__,
            "cores": detect_cores(),
            "jobs": scan_stats.get("jobs"),
            "root": scan_stats.get("root"),
            "files": scan_stats.get("files_seen"),
            "files_unchanged": scan_stats.get("files_unchanged"),
            "entities": scan_stats.get("entities_scanned"),
            "by_language_bytes": scan_stats.get("by_language_bytes"),
            "stage_ms": scan_stats.get("stage_durations_ms"),
            "mine_by_strategy_ms": scan_stats.get("mine_by_strategy_ms"),
            "gaps": gaps_found,
        }
        # Drop None values to keep the log compact.
        record = {k: v for k, v in record.items() if v is not None}
        append_run(record)
    except Exception:
        pass  # never break a scan over a logging hiccup

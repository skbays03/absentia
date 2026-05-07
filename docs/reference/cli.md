# CLI Reference

The lacuna command-line interface. Run any subcommand with `--help`
for the full flag list as printed by argparse — this page is the
narrative companion to those messages.

## Top-level flags

These run before subcommand dispatch. Most affect machine-wide state
in `~/.lacuna/`.

- `--version` — print the installed version and exit.
- `--purge [PATH]` — delete the `.lacuna/` state directory at PATH
  (default: cwd). Removes the entity cache, suppression DB, and
  `last_run.json`. Source code and `lacuna.toml` are untouched.
  Prompts `[y/N]` first; refuses in non-TTY contexts unless `--yes`
  is also passed.
- `--purge-all` — sweep `$HOME` for every `.lacuna/` state
  directory, plus the machine-wide cache at `~/.lacuna/` (the
  calibration cache, settings, and runs log). Same prompt + non-TTY
  refusal as `--purge`.
- `--yes` / `-y` — skip the `[y/N]` prompt on `--purge` /
  `--purge-all`. Use only when scripted; the prompt is the safety net.
- `--jobs-default N` — pin the default worker count for `lacuna
  check` to N. Saved to `~/.lacuna/settings.json`. `--jobs-default 0`
  reverts to auto (half of CPU cores). If N exceeds your detected
  core count you'll be re-prompted to confirm — over-subscribing
  usually slows scans because workers contend for CPU. Per-invocation
  `lacuna check --jobs N` always overrides.
- `--no-color` — force-disable ANSI color in CLI output. Equivalent
  to setting `NO_COLOR=1` in the environment; the flag wins if both
  are set. Honored by both the rich-based output (gap rows,
  prompts) and the raw-ANSI progress UI.
- `--debug` / `-vv` — print extra diagnostic detail to stderr at
  decision points (resolved root, language list, exclude patterns,
  cold-path matching, etc.). Tied to dev work — opposite pole from
  `--quiet`. Doesn't change scan behavior; only what gets printed.
  Sets `LACUNA_DEBUG=1` in the environment so any code that wants
  to add diagnostic prints can check the env var without importing
  CLI internals.

## `lacuna [path]`

Run with no subcommand, from a TTY, to launch the
[interactive TUI](tui-keys.md). Outside a TTY, prints help.

Pass an optional path to open the TUI in a different directory:

```bash
lacuna                  # TUI in cwd
lacuna ~/myrepo         # TUI in ~/myrepo
lacuna /tmp/linux       # TUI in /tmp/linux
```

The path shorthand only fires when the argument is a real,
existing directory and not a known subcommand (`init`, `check`,
`est`, `suppress`). Otherwise argparse handles dispatch normally.

The TUI scans with `--jobs 1` regardless of your `--jobs-default`
setting. Spawn-mode `ProcessPoolExecutor` (the macOS multiprocessing
default) doesn't play well inside Textual's running event loop, so
the safe choice is single-process. The CLI path (`lacuna check`)
keeps full parallelism. Most TUI scans are incremental anyway, so
the threshold for parallelism wouldn't fire.

## `lacuna init [path]`

Bootstraps a project: writes a default `lacuna.toml` and creates a
`.lacuna/` state directory (also added to `.gitignore` if one
exists). Prints a first-scan time estimate at the end so you know
roughly how long the first `lacuna check` will take.

The generated `lacuna.toml` does NOT restrict the language list —
omitting the `languages` key activates every built-in extractor.
Set the key explicitly to scan a subset.

Flags:

- `--force` — overwrite an existing `lacuna.toml`.
- `--quiet` / `-q` — suppress the "Initialized lacuna in PATH"
  message and the first-scan estimate footer. Useful for scripts
  that init then immediately run `lacuna check`.

## `lacuna check [path]`

Batch mode: scans the project, mines patterns, prints gaps. Exits
non-zero on any gap by default; `--max-gaps N` raises the
tolerance. Used by CI and scripting; the TUI is the equivalent for
exploration.

In an interactive terminal (TTY stderr, no `--json`, no `--quiet`),
the scan emits a per-stage display: walking corpus, scanning,
loading store, mining rules, finalizing — each finishing with a ✓
summary line and elapsed time. Live spinners run during long stages
so the tool never feels hung. The display auto-suppresses on
non-TTY, keeping CI logs clean.

Above the per-stage display, a one-line cold-scan estimate preamble
prints (`Scanning N files (M MB) — est. ~Xs at jobs=Y`). Skipped
when `--json`, `--quiet`, or non-TTY.

Flags:

- `--config PATH` — explicit `lacuna.toml` path (default: search
  upward from `path`).
- `--min-confidence FLOAT` — override `mining.min_confidence` from
  config.
- `--min-group-size INT` — override `mining.min_group_size`.
- `--json` — emit machine-readable JSON instead of human text.
- `--quiet` — suppress the stats footer in text mode.
- `--jobs N` (`-j N`) — number of worker processes for the
  parse + extract stage. Defaults to the value pinned by
  `--jobs-default`, otherwise half of detected CPU cores. Set
  `--jobs 1` for a strict single-process run (matches the
  baseline numbers in [the architecture doc](../explanation/architecture.md)).
- `--max-gaps N` — CI tolerance flag. Exit non-zero only when the
  gap count exceeds N. `--max-gaps 0` fails on any gap (matches
  the default behavior); `--max-gaps 5` lets up to 5 gaps slide
  before failing the build. Useful for adopting lacuna on an
  existing codebase without blocking the build the first day.
- `--cold [PATH]` — force re-parse of files at PATH (default:
  the whole scanned root). Recursive — passing a directory
  cold-busts every file under it; a file path cold-busts just
  that file. Tied to dev work: use when you suspect cache
  weirdness, are benchmarking the parse stage, or are validating
  extractor changes. Does NOT delete the cache (next scan without
  `--cold` is back to warm).
- `--language LANG[,LANG]` — restrict the scan to specific
  languages (comma-separated). Overrides `[scan.languages]` in
  `lacuna.toml`. Useful for "I just edited Python; only re-scan
  Python this run." Validates against the registered extractors.
- `--exclude PATTERN` — skip files / directories matching PATTERN
  (POSIX glob, e.g. `'**/vendor/**'`). May be passed multiple
  times (`--exclude tests --exclude docs`). *Appends* to
  `[scan.exclude]` in `lacuna.toml` rather than replacing — the
  config typically holds long-lived excludes (vendored deps,
  build artifacts) and the flag adds one-off exclusions for this
  run. Pattern matching uses `PurePosixPath.full_match`, so `**`
  segments work as expected.

## `lacuna est [path]` (alias: `lacuna estimate`)

Predicts cold-scan time *without* actually scanning. Walks the
project, applies a calibrated cost model, prints a headline total
with a confidence band and a per-jobs breakdown table.

The headline reads:

```
Total check estimate     ~7m 30s ± 1m 30s   (medium confidence)
  components             parse 21s + mine 7m 9s at default jobs (5) · estimated
  calibration covers your language mix; refined by 8 prior runs
```

Three confidence levels — `high`, `medium`, `low` — derived from
how much of your project's language mix the calibration covers,
the calibration's age, and how many prior `lacuna check` runs
have accumulated in `~/.lacuna/runs.jsonl`. The band tightens as
you accumulate samples: every cold scan automatically refines the
mining-throughput model, no explicit recalibration required.

For the full methodology — cost model, Amdahl's law, calibration
internals, runs aggregation, accuracy expectations — see
[the estimator doc](../explanation/estimator.md).

On first run (when no `~/.lacuna/calibration.json` exists), prompts
you to calibrate against a corpus on your machine. The cache is
re-prompted when lacuna upgrades, when the core count changes
(e.g. you swap laptops), or when 90 days pass.

Flags:

- `--config PATH` — explicit `lacuna.toml` path (default: search
  upward from `path`). Mirrors `lacuna check --config`.
- `--jobs N` (`-j N`) — override which worker count is highlighted
  as the headline. The full per-jobs table still renders; this
  only changes which row is the bottom-line prediction. Defaults
  to `--jobs-default` or half of detected CPU cores.
- `--json` — emit machine-readable JSON instead of the human
  estimate report. Stable shape:

      {
        "root":                  "/path/to/scanned/dir",
        "files":                 8456,
        "bytes":                 45678901,
        "cpu_count":             10,
        "headline_jobs":         5,
        "calibrated":            true,
        "calibrated_at":         "2026-04-30T12:34:56Z",
        "model_mining_tail_s":   12.3,
        "model_mining_source":   "aggregated from 47 prior runs",
        "observed_cold_scan_s":  18.4,
        "observed_jobs":         5,
        "runs_aggregated":       47,
        "parallel_fraction":     0.85
      }

  Use case: a CI step that decides whether to skip a long scan
  based on the cost prediction.
- `--quiet` / `-q` — collapse the report to its bottom-line
  "Total check estimate" line. Useful when piping into shell
  scripts or composing into larger CI flows. Implies
  non-interactive (no calibration prompts).
- `--recalibrate` — force re-running calibration even if a fresh
  cache exists. Calibration runs against the `path` argument
  (default: cwd), so `lacuna est ~/myrepo --recalibrate` produces
  a calibration tuned to that codebase's language mix.
- `--use-synthetic` — calibrate against a bundled synthetic Python
  corpus instead of `path`. Useful when the current directory is
  empty or too small (< 30 files / < 100 KB) for reliable
  calibration.
- `--history` — print recent `lacuna check` runs from
  `~/.lacuna/runs.jsonl` (when, jobs, files, check time, parse
  time, mine time, root) plus the aggregated mining throughput
  across compatible runs. Useful for auditing what data the
  prediction is based on.
- `--cold [PATH]` — scope the prediction to PATH (default: the
  whole scanned root). est is always a cold-scan prediction, so
  this is functionally identical to passing PATH as the positional
  argument; the symmetry with `check --cold` keeps muscle memory
  consistent across subcommands.
- `--language LANG[,LANG]` — scope the prediction to specific
  languages. Mirrors `lacuna check --language`.
- `--exclude PATTERN` — skip files / directories matching PATTERN
  from the corpus walk used for the prediction. May be passed
  multiple times. Mirrors `lacuna check --exclude`.

## `lacuna suppress [gap-id] [path]`

Marks a gap as known / intentional so it stops appearing in
`lacuna check` output and the TUI Gaps view. Equivalent to pressing
`s` in the TUI.

The optional `path` positional argument is the project root
(default: cwd). It's symmetric with the positional `path` on
`init`, `check`, and `est`.

Flags:

- `--reason "..."` — required when adding a suppression. Describes
  why the gap is intentional; surfaced in the TUI and persisted to
  the state DB.
- `--remove` — remove an existing suppression.
- `--list` — print all current suppressions and exit.
- `--path DIR` — *[deprecated]* project root. Use the positional
  argument instead. Kept for backward compatibility with existing
  scripts; emits a one-line deprecation hint when used.

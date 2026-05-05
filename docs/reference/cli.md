# CLI Reference

The lacuna command-line interface. Run any subcommand with `--help`
for the full flag list as printed by argparse — this page is the
narrative companion to those messages.

## `lacuna`

Run with no subcommand, from a TTY, to launch the
[interactive TUI](tui-keys.md). Outside a TTY, prints help.

## `lacuna init [path]`

Bootstraps a project: writes a default `lacuna.toml` and creates a
`.lacuna/` state directory (also added to `.gitignore` if one
exists). Prints a first-scan time estimate at the end so you know
roughly how long the first `lacuna check` will take.

Flags:

- `--force` — overwrite an existing `lacuna.toml`.

## `lacuna check [path]`

Batch mode: scans the project, mines patterns, prints gaps. Exits
non-zero if gaps remain (configurable via `--max-gaps`, planned).
Used by CI and scripting; the TUI is the equivalent for
exploration.

In an interactive terminal, prints a one-line cold-scan estimate
preamble (`Scanning N files (M MB) — est. ~Xs at jobs=Y`) before
starting. Suppressed when output is JSON, when `--quiet` is set,
or when stderr isn't a terminal — keeps CI logs clean.

Flags:

- `--config PATH` — explicit `lacuna.toml` path (default: search
  upward from `path`).
- `--min-confidence FLOAT` — override `mining.min_confidence` from
  config.
- `--min-group-size INT` — override `mining.min_group_size`.
- `--json` — emit machine-readable JSON instead of human text.
- `--quiet` — suppress the stats footer in text mode.
- `--jobs N` (`-j N`) — number of worker processes for the
  parse + extract stage. Defaults to half of detected CPU cores.
  Set `--jobs 1` for a strict single-process run (matches the
  baseline numbers in [the architecture doc](../explanation/architecture.md)).

## `lacuna est [path]` (alias: `lacuna estimate`)

Predicts cold-scan time *without* actually scanning. Walks the
project, applies the calibrated cost model, prints a jobs-vs-time
ASCII table.

On first run (when no `~/.lacuna/calibration.json` exists), prompts
you to calibrate against a corpus on your machine. The cache is
re-prompted when lacuna upgrades, when the core count changes
(e.g. you swap laptops), or when 90 days pass.

For full methodology — cost model, Amdahl's law, calibration
internals, accuracy expectations — see
[the estimator doc](../explanation/estimator.md).

Flags:

- `--recalibrate` — force re-running calibration even if a fresh
  cache exists.
- `--use-synthetic` — calibrate against a bundled synthetic Python
  corpus instead of `path`. Useful when the current directory is
  empty or too small (< 30 files / < 100 KB) for reliable
  calibration.

## `lacuna suppress [gap-id]`

Marks a gap as known / intentional so it stops appearing in
`lacuna check` output and the TUI Gaps view. Equivalent to pressing
`s` in the TUI.

Flags:

- `--reason "..."` — required when adding a suppression. Describes
  why the gap is intentional; surfaced in the TUI and persisted to
  the state DB.
- `--remove` — remove an existing suppression.
- `--list` — print all current suppressions and exit.
- `--path DIR` — project root (default: cwd).

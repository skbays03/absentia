# Changelog

All notable changes to lacuna will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Minimum Python is now 3.13** (was 3.11). No active downstream
  users yet, so the cost is zero and the cleanup is real:
  ``os.process_cpu_count()`` (cgroup-aware on Linux containers) is
  now called directly across `parallel.py`, `calibration.py`,
  `estimator.py`, and `scripts/diagnose_scan.py` instead of the
  previous `hasattr(os, "process_cpu_count")` fallback dance.
  CI matrix narrowed to 3.13 + 3.14; ruff `target-version` and mypy
  `python_version` bumped to match.
- **Mining stage is ~30× faster on large corpora.** Linux kernel
  scan: mine 320.8 s → 10.7 s, gap counts byte-identical (15,330).
  Three landed wins: (a) ``find_symmetry_gaps`` refactor —
  pre-compute ``_short_name(ent)`` once per entity, look up by name
  in O(1) instead of an O(P×N) per-pair-per-entity scan; (b)
  hatch-mypyc compiles ``mining.py`` + ``symmetry.py`` to native C
  extensions (wheels published per OS × arch via the new
  ``wheels.yml`` cibuildwheel workflow); (c) ``mining_worker_cap()``
  lifts the ThreadPool cap from 4 → 7 on free-threaded Python
  (3.13t / 3.14t) so each strategy can saturate its own core. No
  behavior change for users on regular CPython — the cap stays 4
  and the speedup comes entirely from (a) + (b).
- **Progress bar no longer stair-steps in narrow tmux panes.**
  Replaced fixed ``_LINE_WIDTH = 120`` padding with a
  ``_truncate_visible(s, width)`` helper that cuts to the live
  ``shutil.get_terminal_size().columns`` and uses ``\033[K``
  (Erase-in-Line) to clear instead of trailing spaces. Wire bytes
  shrink ~44%; ``\033[F`` cursor recovery now lands correctly on
  panes < 120 cols.

### Added

- **Multi-worker progress UI.** ``lacuna check --jobs N`` in
  interactive text mode now shows one sub-line per worker (parse
  stage) and one sub-line per running strategy (mining stage),
  each tagged with a per-language color: Python blue, Rust orange,
  Go cyan, Ruby red, etc. Uses
  ``multiprocessing.Manager().Queue()`` + a daemon drain thread
  to feed worker (id, language, path) updates into
  ``ProgressBar.set_workers()``. Backward-compatible: callers that
  don't call ``set_workers()`` get the original single-sub-line
  behavior; non-TTY and ``--jobs 1`` paths skip the queue entirely.
- **Per-strategy mining timings in ``runs.jsonl``.** The mining
  stage now records ``mine_by_strategy_ms`` (one entry per
  strategy: symmetry pairs, call-pair, frequency:decorator, etc.)
  alongside the existing ``stage_ms`` totals. Surfaces in
  ``lacuna est --history`` and turns the previously opaque mining
  tail into a profiling-grade signal.
- **`--cold [PATH]` flag on `lacuna check` and `lacuna est`.** Dev-
  time cache-bust: forces re-parse of files at PATH (default: the
  whole scanned root). Recursive — passing a directory cold-busts
  every file under it. Doesn't delete the cache (next scan without
  the flag is back to warm). For `est`, scopes the prediction to
  PATH; functionally equivalent to passing PATH as positional, but
  symmetric with `check --cold` for muscle memory.

- **CLI flag pass — symmetric coverage + scope flags.** Closes the
  asymmetries that built up across subcommands.
  - **`est`** gained `--config CONFIG`, `--jobs N`, `--json`,
    `--quiet` to mirror what `check` already had. `--quiet`
    collapses the report to its bottom-line "Total check estimate"
    line; `--json` emits a stable `{root, files, bytes,
    headline_jobs, ...}` shape suitable for CI cost-prediction
    gates.
  - **`init`** gained `--quiet` so scripts that init then
    immediately run check don't have to redirect stdout.
  - **`suppress`** now accepts the project root as a positional
    argument (`lacuna suppress <gap_id> <path>`) for symmetry with
    init/check/est. The legacy `--path` is preserved as a deprecated
    alias and emits a one-line hint when used.
  - **`check` and `est`** gained `--language LANG[,LANG]` (override
    `[scan.languages]` for one run) and `--exclude PATTERN`
    (action=append; appends to `[scan.exclude]`). `--exclude`
    activates a new glob-based path filter in `find_source_files`
    using `PurePosixPath.full_match` (3.13+); `**/vendor/**` etc.
    work as expected.
  - **Top-level `--no-color`** and **`--debug` / `-vv`**.
    `--no-color` sets `NO_COLOR=1` before color-detection runs
    (flag wins over env, both supported). `--debug` enables
    diagnostic prints to stderr at config / scope / cold-path
    decision points; opt-in, gated on `LACUNA_DEBUG=1` so other
    code can check it without importing CLI internals.

  Use cases: validating extractor changes (no stale cached
  entities), benchmarking the parse stage in isolation, debugging
  suspected cache weirdness without nuking ``.lacuna/``.

- **Mining-stage progress detail (phase + counter + current item).**
  Each running strategy now surfaces a live ``[phase] N/M item``
  sub-line so the user sees what the strategy is actually doing
  right now, not just that it's busy. Each mining strategy
  function (``mine``, ``find_symmetry_gaps``, ``find_call_pair_gaps``,
  ``find_series_gaps``, ``mine_symmetry_pairs``) gains an optional
  ``progress_hook=None`` kwarg; the cli builds a label-bound,
  50 ms-throttled hook and renders into ``mine_spinner.set_workers``.
  Inner-loop hot spots additionally use bitmask sampling so the
  perf cost is below the noise floor of ``time.perf_counter``.
  No-op when ``progress_hook is None`` (jobs=1 / non-TTY).



- **Per-stage progress UI.** `lacuna check` in interactive text
  mode now emits five ✓ summary lines (walk / parse / store / mine
  / finalize), each with elapsed time, plus live spinners during
  long stages. Mining-stage spinner sub-line surfaces per-strategy
  completion ("3/7 done · last: symmetry pairs · 47 rules so far"),
  turning the previously silent multi-minute mining tail into a
  visible, diagnosable stage. Auto-suppresses on non-TTY.
- **`--max-gaps N` CI tolerance flag.** `lacuna check --max-gaps 5`
  exits non-zero only when the gap count exceeds 5. Default
  behavior (no flag) keeps the strict "any gap fails" exit policy.
  Useful for adopting lacuna on an existing codebase without
  blocking the build the first day.
- **`lacuna --jobs-default N`** — pin the default worker count for
  `lacuna check`. Saved to `~/.lacuna/settings.json`. If N exceeds
  detected core count, re-prompts to confirm; non-TTY contexts
  refuse without `--yes`. `--jobs-default 0` reverts to auto.
- **`lacuna --purge [PATH]` and `lacuna --purge-all`.** Top-level
  flags to remove `.lacuna/` per-project state and the machine-wide
  cache, with a `[y/N]` prompt + non-TTY refusal.
- **`lacuna est --history`** — print the recent `lacuna check`
  runs that feed the estimator, plus the aggregated mining
  throughput. Useful for auditing what data the prediction is
  based on.
- **Continuous-calibration runs log** at `~/.lacuna/runs.jsonl`.
  Every successful `lacuna check` appends a row (timestamp,
  version, cores, jobs, root, language-byte shape, per-stage
  timings). `lacuna est` aggregates ≥3 fresh compatible runs into
  a refined `mining_seconds_per_byte`, replacing the static
  calibration value with real-world data. The more often you run
  check, the more accurate the prediction becomes — no explicit
  recalibration step.
- **`est` headline + confidence band.** "Total check estimate
  ~X ± Y (high/medium/low confidence)" lands at the top of the
  report, with reasoning that ties the band to corpus-similarity,
  calibration age, and accumulated runs.
- **`est` per-stage breakdown** when prior cold-scan timings
  exist. Shows where time actually went (walk / parse / store /
  mine / finalize) on the previous run.
- **`est` parse + mine_tail = check column** in the per-jobs
  table. Mining is treated as a fixed serial tail (it doesn't
  scale with workers past the 4-thread cap), so the new column
  shows full `lacuna check` time at each `--jobs` setting, not
  just parse.
- **Calibrated mining-tail prediction** (`mining_seconds_per_byte`
  in `calibration.json`) so `lacuna est` can predict full check
  time before the user has run check even once.
- **Per-stage timings persisted** to `.lacuna/last_run.json`
  (`stage_durations_ms` map for walk / parse / store / mine /
  finalize) so the est report can cite real ground truth.

### Changed

- **`lacuna init` no longer hardcodes `languages = ["python"]`.**
  The generated `lacuna.toml` comments out the line, so omitting
  it activates every built-in extractor (17 covering 16
  languages). Set the key explicitly to scan a subset.
- **TUI scans with `jobs=1`.** Spawn-mode `ProcessPoolExecutor`
  (the macOS multiprocessing default) doesn't play well inside
  Textual's running event loop and surfaces as
  `bad value(s) in fds_to_keep` on Mac. Single-process avoids
  the issue; the CLI path keeps full parallelism.

### Fixed

- **Calibration speed-factor false-low on slow-overhead boxes.**
  Pipeline overhead is now measured on an empty corpus and
  subtracted before computing `machine_speed_factor`, so a small
  calibration corpus on a slow filesystem (WSL `/mnt/c/` etc.)
  no longer reads as 0.10× when the actual throughput is
  reasonable.
- **`call_pair` mining O(N²) hang on kernel-scale corpora.**
  Rewrote the violator-emission loop with a precomputed
  `callers_by_name` index; the inner step is now O(1) instead of
  O(N) per emitted pair. The Linux kernel scan that hung
  silently for minutes after the parse bar reached 100% now
  completes in expected time.

### Performance

(Pre-optimization headline still cited; new measurements pending
on Shawn's hardware. See `~/Desktop/lacuna_doc_todos.txt §2`.)

---

## Earlier (rolled into [Unreleased] before first release)

### Added

- **Series-gap detection.** Fourth mining strategy. Detects missing
  numeric indices in same-directory file sequences:
  ``migrations/0001_*.py``, ``0002_*.py``, ``0004_*.py`` →
  ``missing 0003_*.py``. Clusters by sequential proximity (default
  max gap = 5) so a stray ``0099_*`` doesn't create a 96-element
  gap range against an early cluster. The fourth latin-flavored
  category from lacuna_plan; lacuna now catches gaps in series,
  not just gaps in patterns.
- **Call-pair detection.** Mines paired-call symmetries within
  function scope: ``9 of 10 functions calling bus.subscribe also
  call bus.unsubscribe`` flags the 10th. Catches project-specific
  resource pairs (subscribe/unsubscribe, audit.begin/audit.commit,
  trace.start/trace.stop, custom acquire/release APIs) that no
  off-the-shelf linter knows about. Conservative defaults
  (min_confidence=0.9, min_support=5) filter out noise from
  language built-ins. Doesn't try control-flow analysis — that's
  linter territory; lacuna stays at the project-convention layer.
- **Cross-strategy gap dedup.** Frequency mining, symmetry pairs,
  and call-pair mining can each independently flag the same entity
  for the same missing thing. A post-mining pass collapses
  duplicates so users see each gap once (highest-confidence rule
  wins); rules stay distinct in the Rules view for transparency.
- **Symmetry-pair detection.** A second mining strategy that catches
  structural gaps the frequency engine misses — a class with
  ``__enter__`` and no ``__exit__``, a migration with ``upgrade()``
  and no ``downgrade()``. Two sources of pairs:

  - **Hardcoded language protocols** (``__enter__/__exit__``,
    ``__aenter__/__aexit__``) — Python's runtime requires both, so
    these aren't conventions, they're contracts.
  - **Auto-mined from the corpus** — pairs of method/function names
    that co-occur in ≥80% of scopes containing either one with at
    least one violator. Catches project-specific conventions
    (``setUp/tearDown`` if you use unittest, ``upgrade/downgrade``
    if you use alembic, ``register/unregister`` if your project
    has an event bus, etc.) without a hardcoded list — same
    philosophy as the rest of the engine: "the rules come from
    your code itself."
- **Sibling-test detection.** A new corpus-level enrichment pass
  computes a `sibling_test` feature for every non-test function;
  mining over it produces "8/10 functions in src/api/ have a sibling
  test; this one doesn't" gaps. Closes the README's long-standing
  promise. Conventions covered: `tests/<rest>/test_<name>.py`,
  flat `tests/test_<name>.py`, in-tree `<dir>/test_<name>.py`,
  and Go-style `<name>_test.<ext>`.
- **17 built-in extractors covering 16 languages** built on
  tree-sitter: Python, JavaScript, TypeScript, TSX, Rust, Go,
  Java, Ruby, C#, Swift, C, C++, PHP, Kotlin, Scala, Lua, Bash.
  TS and TSX share a tree-sitter grammar but emit distinct
  extractors. Pluggable via the `lacuna.extractors` entry-point
  group.
- **Mining engine** — frequent-itemset over `(group, feature)`
  pairs with confidence threshold. Three built-in selectors
  (`directory`, `decorator`, `parent_class`).
- **Stable IDs** for entities (qualified-name derived) and gaps
  (hash-derived short IDs); suppressions remain stable across runs.
- **SQLite-backed incremental cache.** Content-hash diffing means
  warm rescans complete in milliseconds.
- **Two front-ends.** A Textual TUI for interactive exploration
  (Gaps / Rules / Groups / Stats views; filter, follow, suppress,
  watch mode) and `lacuna check` for CI batch use.
- **`e` explain modal** in the TUI — peek-style "why was this gap
  flagged" panel that doesn't move you off the gaps list.
- **Parallel parse + extract** across a worker pool. `lacuna check
  --jobs N`; default is half of detected CPU cores. Workers spawn
  lazily and skip the pool entirely on small jobs.
- **`lacuna est`** — cold-scan time estimator. Walks the corpus,
  applies a calibrated cost model (per-language throughput +
  Amdahl's law), prints a jobs-vs-time table.
- **First-run calibration** with cache at `~/.lacuna/calibration.json`.
  Multi-jobs Amdahl `p` fitting, per-language BPS measurement,
  bundled synthetic corpus (`--use-synthetic`), version +
  core-count + 90-day staleness invalidation.
- **Estimate integration points.** Preamble line in `lacuna check`
  text mode; first-scan footer in `lacuna init`; transient
  "estimating ~Xs" subtitle in the TUI on cold scans.
- **Apache 2.0 license** (see `LICENSE` and `NOTICE`).
- **CI pipeline** — pytest matrix on Python 3.11/3.12/3.13, ruff,
  mypy (lenient), `mkdocs build --strict`.
- **Documentation** via Diátaxis + mkdocs-material:
  - Tutorials: quickstart
  - Reference: CLI, `lacuna.toml`, selectors, TUI keybindings
  - Explanation: what-is-negative-space, why-no-llm,
    how-mining-works, architecture-and-performance, the
    cold-scan time estimator

### Performance

Benchmarked on 16 large public corpora totaling ~2.4M entities.
Headline: lacuna scans the entire Linux kernel — 666,574 entities
across ~30 million lines of C — in 96.7s on a single Python process
on an M-series MacBook. Full table in
`docs/explanation/architecture.md`.

# Changelog

All notable changes to absentia will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

### Changed

### Fixed

## [1.0.1] - 2026-05-09

Same-day patch release fixing a `--version` drift bug discovered
minutes after v1.0.0 hit PyPI, plus install-experience improvements.

### Added

- **`[project.urls]` for the PyPI sidebar.** Repository, Issues,
  Changelog, Documentation, Release Notes — all surface as
  clickable links on `https://pypi.org/project/absentia/`. v1.0.0
  shipped with these empty (the prior config had them deferred
  per DEFERRALS.md, predating the PyPI publish).

- **`uv tool install absentia` documented as a co-recommended
  install path.** README install section now leads with two
  parallel options (`uv tool` and `pipx`), per-OS bootstrap
  tables for both, and a callout for the PEP 668
  `error: externally-managed-environment` that modern Debian /
  Ubuntu / WSL / Fedora users hit on bare `pip install`. Plus the
  venv form for using absentia as a library.

### Fixed

- **`absentia --version` reported the wrong version.** v1.0.0
  installed correctly (pip reported `absentia-1.0.0`) but
  `absentia --version` printed `absentia 0.0.1` because
  `src/absentia/__init__.py` had `__version__ = "0.0.1"`
  hardcoded since the project's first commit. `scripts/release.sh`
  bumps `pyproject.toml` but doesn't touch `__init__.py`, so the
  two drifted on every release. Switched `__version__` to read
  from `importlib.metadata` at import time — `pyproject.toml`
  is now the single source of truth, no future drift possible.

- **Build-system metadata mismatch caught the v1.0.0 first publish
  attempt.** The PEP 639 SPDX-string license form
  (`license = "Apache-2.0"`) requires Metadata 2.4 in the emitted
  wheel, but the hatchling cibuildwheel v2.21 resolves under its
  `packaging==24.1` constraint emits Metadata 2.3 — twine + PyPI
  rejected v1.0.0's initial publish with
  `InvalidDistribution: license-expression introduced in metadata
  version 2.4, not 2.3`. Switched to the pre-PEP-639 table form
  (`license = { text = "Apache-2.0" }`) which works on any
  hatchling version. v1.0.0 was retagged + republished before any
  user could pull the broken artifact. The long-term fix
  (upgrading cibuildwheel + hatchling so 2.4 is emitted
  consistently) is left for a follow-up.

## [1.0.0] - 2026-05-09

### Added

- **Five mining strategies — gap-detector roadmap closed.**
  Frequency, symmetry, call-pair, series, closure. Eight new gap
  shapes shipped on top of the prior frequency / symmetry / series
  base:
  - `has_post_init` (Item A) — config-validation gap. Flags
    dataclass-shaped classes in directories where most siblings
    define `__post_init__` validation.
  - `module` entity + `has_all_export` (Item B) — public-surface
    gap. Every Python file emits a module-scope entity carrying
    whether the file declares `__all__`. Mining over the directory
    selector finds the module that forgot to advertise its API.
  - `call_kwargs` (Item C) — logging / tracing-convention gap.
    "Every endpoint passes `request_id=` to some call; this one
    doesn't."
  - `entry_point_registered` (Item D) — meta gap. Reads
    `pyproject.toml`'s `[project.entry-points]`; flags classes that
    sit in a directory full of registered plugins but aren't
    themselves registered. Skips ABC subclasses.
  - **Letter series** (Item E) — `part_a.md`, `part_b.md`,
    `part_d.md` → flag missing `part_c.md`. Case- and width-
    preserving.
  - **Version-directory series** (Item F) — `api_v1/`, `api_v2/`,
    `api_v4/` → flag missing `api_v3`. No new entity kind required;
    walks file-path-implied directories.
  - **Ordinal alphabets** (Item G) — `TestUserCRUD` with
    `test_create / test_read / test_update` but no `test_delete` →
    flag the missing slot. Synonym-aware (`read` / `get` / `list` /
    `find` / `findAll` are the same slot) + class-name-hint gating
    so lifecycle classes don't false-fire.
  - **Closure pass — defined-but-never-used classes** (Item H).
    Language-agnostic: the inverse-reference index is built from
    `calls` / `parent_class` / `decorator` features that every
    extractor already emits, plus a corpus-text identifier scan
    catches references the feature index misses (TypeScript type
    annotations, NestJS module-imports arrays, isinstance args).
    Filters: skip private-named classes, skip test files, skip
    entry-point-registered classes. Tokenize-once with
    `Counter.update(filter)` keeps the kernel-scale closure pass
    at ~16 s of pure regex work.

  All five strategies emit the same `Rule` + `Gap` shape so the TUI,
  suppression system, formatters, and cross-strategy dedup keep
  working unchanged.

- **TUI debug log + `absentia report` crash-recovery flow.** The
  TUI journals every key press and action to `~/.absentia/tui.log`
  (rotated at 1 MB) so post-mortem debugging has a reproducible
  event trail. Catastrophic crashes prompt `File a GitHub issue
  with this log? [y/N]` inline before re-raising — the new
  `absentia report` subcommand composes a prefilled issue with
  system info + the last 200 log lines and either fires
  `gh issue create --web` or falls back to a `webbrowser.open` URL.
  `absentia report --no-prompt` skips the standalone confirmation
  for users who've already decided.

- **TUI: collapsible info panels (`i` key).** The bottom-of-pane
  detail + code preview now sit in a wrapper container docked to
  the bottom of the table panel, so they stay flush with the
  footer regardless of available height. Press `i` to collapse
  the wrapper; the DataTable claims the freed rows and a one-line
  hint shows the shortcut to bring them back. Toggle is sticky
  for the session.

- **TUI: footer split into visible + hidden binding tiers.** 22
  bindings had grown past what any terminal narrower than ~200
  columns could render in a single footer line. Visible set is now
  10 entries (view switchers, Quit, Explain, Suppress, Filter,
  Help); the other 12 stay fully keyboard-routable but hidden via
  Textual's `show=False`. Discovery surface unchanged — `?` Help
  and `Ctrl+P` Command palette list every binding.

- **CI: sdist build + install smoke-test on every push.** New
  `sdist-build` job in `ci.yml` builds the source distribution,
  inspects its contents, installs it in a clean venv, and imports
  the package — catches packaging breakage (missing files, wrong
  build-system entries, stale package-data globs) pre-merge
  instead of at release-cut time.

- **Repo: CODE_OF_CONDUCT.md added.** Contributor Covenant 2.1,
  enforcement contact filled in.

- **Wheels matrix: dropped macos-13 (Intel) row.** GitHub Actions'
  Intel Mac runner pool is being phased out; live availability is
  poor enough that jobs routinely sit in the queue 10+ minutes
  before pickup. Apple stopped selling Intel Macs in mid-2023.
  Intel-Mac users get the sdist fallback (compiles via mypyc at
  install time) — same coverage neighborhood as cryptography,
  lxml, and numpy ditched macos-13 in 2025.

- **Color palette: 3 of 6 language-color collisions cleared.**
  Added a `WHITE` ANSI constant; reassigned `swift` → YELLOW,
  `ruby` → BRIGHT_GREEN, `kotlin` → WHITE so they no longer
  collide with javascript / java / php respectively. Three
  acceptable collisions remain (typescript+tsx family, c+cpp
  family, scala+csharp coincidence).

### Changed

- **PyPI publish migrated to OIDC Trusted Publishing.** Replaces
  long-lived `PYPI_API_TOKEN` / `TEST_PYPI_API_TOKEN` repo secrets
  with short-lived OIDC tokens issued by GitHub Actions and
  verified by PyPI against pre-registered Trusted Publisher
  entries (one for `pypi.org`, one for `test.pypi.org`, both bound
  to this workflow's filename + repo identity). The action
  auto-detects OIDC when `id-token: write` is granted at the
  workflow level and no `password:` is supplied. There's no
  long-lived credential left to leak from repo secrets — a
  compromised repo can mint at most one publish using the
  ephemeral token, not arbitrary future publishes.

- **Performance numbers re-measured against the current code.**
  The `~28 s warm / ~50 s cold` kernel headline drifted as
  optimizations landed; re-ran a clean cold + warm scan and
  updated the README + architecture-doc tables. Now `~24 s warm /
  ~48 s cold` end-to-end at default jobs (5) on a 10-core M-series
  MacBook, with stage breakdown `parse 8 / mine 12 / store 2`
  warm, `parse 31 / mine 12 / store 3` cold. Mining-stage speedup
  story tightened to `~25×` from the pre-mypyc 5-minute baseline.
  Throughput section in the architecture doc extended to quote
  both the single-process `~7,200 ent/s` and default-jobs
  `~14,400 ent/s` figures (previously only the slower
  single-process measurement appeared, understating real-world
  parallel throughput).

### Fixed

- **`absentia report` GitHub repo path.** The first cut pointed at
  a non-existent repo path (the local folder convention, not a
  real GitHub org), so every crash report would 404 before
  reaching the issue tracker. Repointed at the actual remote.

- **JavaScript extractor walks IIFE bodies.** Pre-ES-modules JS
  routinely encapsulates module state via the revealing-module /
  IIFE pattern (`const App = (() => { function init() {} })()`).
  The extractor previously treated these as unanalyzable
  `call_expression` bindings and skipped every encapsulated
  function. Smoke-tested 19 → 177 entities (9.3×) on a
  representative IIFE-heavy frontend.

- **Ruby extractor recurses into nested classes / modules.**
  `module Foo; class Bar; def baz` chains used to emit only the
  outer module — Sinatra's `lib/sinatra/base.rb` (68 KB) yielded
  1 entity instead of 144. Recursive body-walker now extracts
  the full nesting tree plus singleton methods (`def self.foo`).

- **Lua extractor handles table-of-functions modules.**
  `M.foo = function() end` and `local foo = function() end` are
  the dominant patterns in plenary.nvim and most Neovim plugins;
  the prior extractor only walked `function_declaration` nodes
  and missed these entirely. plenary.nvim now extracts 372 →
  580 entities (+56%, density 696 → 1085 ents/MB).

- **Build-system PEP 639 schema-fork mitigation.** Removed the
  explicit `license-files = ["LICENSE", "NOTICE"]` array from
  `pyproject.toml` — newer hatchling versions accept the array
  form but cibuildwheel's bundled constraints pin packaging
  to a version below where the array form was finalized, so
  isolated wheel builds reject the field. Default discovery
  globs (LICENSE\*, NOTICE\*) cover the same files without
  the version-skew failure mode.

- **`scripts/release.sh` portability fixes.** BSD `sed` (macOS)
  silently accepted but didn't act on the GNU-only `0,/.../`
  address form, so version bumps appeared to succeed and then
  failed verification. Replaced with a portable awk pass.
  Separately, the auto-generated commit message now carries
  the `Authored-by:` trailer the repo's commit-msg hook
  requires; rollback on hook rejection unstages files in
  addition to discarding working-tree changes.

### Changed

- **CI split: cheap gates per-push, heavy gates per-tag.** `ci.yml`
  now runs only `lint` + `fingerprint-bump` on every push to main /
  PR — both are seconds-cheap and earn their keep pre-merge.
  The full `pytest` matrix (3.13 + 3.14), `mypy`, and
  `mkdocs --strict` moved to the new `release-checks.yml` workflow,
  triggered on `push: tags: ['v*.*.*']` and `workflow_dispatch`.
  The local `scripts/local_ci.sh` (and the `.githooks/pre-push`
  hook that calls it) still runs the full set on the developer's
  machine before push, so heavy gates aren't bypassed — they're
  just deferred to the release boundary instead of duplicated per
  push.

- **Minimum Python is now 3.13** (was 3.11). No active downstream
  users yet, so the cost is zero and the cleanup is real:
  ``os.process_cpu_count()`` (cgroup-aware on Linux containers) is
  now called directly across `parallel.py`, `calibration.py`,
  `estimator.py`, and `scripts/diagnose_scan.py` instead of the
  previous `hasattr(os, "process_cpu_count")` fallback dance.
  CI matrix narrowed to 3.13 + 3.14; ruff `target-version` and mypy
  `python_version` bumped to match.
- **Mining stage is ~23× faster on large corpora.** Linux kernel
  scan: mine ~5 min → ~14 s, gap counts byte-identical to the
  pre-optimization baseline at the same ``feature_kinds`` set.
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

### Performance

- **Enrichment-stage hot-path optimization** (opt #12 follow-up).
  Profile-guided pickup (`scripts/profile_scan.py`) on a cold Linux
  kernel scan surfaced `enrich_sibling_tests` consuming 7.8 s of
  cProfile-cumulative time (~6% of profiled wall-clock) — about 4×
  the back-of-envelope estimate that informed the pass on opt #8.
  Three contained changes in `src/absentia/enrichment.py`:
  - Memoize `_candidate_test_files` per source-file path inside the
    enrichment loop. ~640k entities across ~65k unique source files
    on the kernel = ~10× redundant candidate-list computations
    eliminated.
  - Memoize `is_test_file` calls per source-file path in the same
    loop (same hit-rate logic).
  - Tighten `is_test_file` itself: `str.startswith(tuple)` and
    `str.endswith(tuple)` use a single C call vs N Python iterations
    of an `any(... for p in PREFIXES)` generator.
  - Hoist the `frozenset()` default in the inner `dict.get` to a
    module-level `_EMPTY_SET` sentinel so the fallback isn't
    re-allocated on every of millions of lookups.
  Validation (cold kernel scan, jobs=1):
  - Function-call count: 313 M → 273 M (-12.9 %).
  - `enrich_sibling_tests` cumulative under cProfile: 7.84 s →
    2.44 s (-69 %).
  - Real wall-clock: 95.6 s → 94.4 s on cold scan (-1.3 %); 26.9 s
    → 26.1 s on warm scan (-3.0 %, where enrichment is a bigger
    fraction of total). Smaller than the cProfile delta suggested
    because cProfile inflates pure-Python overhead more than C-
    extension calls — and this optimization eliminated only the
    pure-Python work.
  Gap counts unchanged across all corpora (semantically equivalent;
  validated via `tests/test_corpus_regression.py`).

### Added

- **`scripts/profile_scan.py` — repeatable cProfile harness.**
  Optimization-plan #12 (profile-guided pickup) made repeatable.
  `python scripts/profile_scan.py /tmp/linux --top 25` runs `absentia
  check` under cProfile and dumps top-N hotspots by cumulative time,
  total time, and call count. Defaults to `--jobs 1` for clean
  profile data; `--no-cold` skips the cache-blow-away for warm
  rescan profiles. Mypyc-compiled hot paths (`mining.py`,
  `symmetry.py`) appear as opaque native calls — that's intentional;
  the script answers "what else is slow?" not "how fast is the part
  we already optimized?".

- **`scripts/release.sh` — interactive release CLI.** Bumps
  `pyproject.toml`, promotes CHANGELOG `[Unreleased]` to a
  versioned heading, commits, annotated-tags, and pushes (each
  step rolls back on failure). Tag push triggers
  `release-checks.yml` + `wheels.yml`. Modes: interactive menu
  (validate / patch / minor / major / set / cancel) plus
  non-interactive flags (`--patch`, `--minor`, `--major`,
  `--set=X.Y.Z`, `--validate`, `--no-verify`). `--validate`
  dispatches `release-checks.yml` on the current branch via
  `gh workflow run` without bumping anything — useful for
  catching Python-3.14-specific failures the local pre-push
  hook can't reproduce.

- **`EXTRACTOR_FINGERPRINT` cache-invalidation salt.** The per-file
  content hash that decides "use cached extract or re-parse?" is
  now salted with `extractors.EXTRACTOR_FINGERPRINT`. Bumping the
  constant invalidates every cached entry on the next scan — so
  when a release ships new feature_kinds, new entity kinds, or
  extractor logic fixes, users automatically pick up the new
  behavior on the first `absentia check` after the upgrade without
  having to know to `--cold` or `--purge`. Bump policy + history
  live as a docstring on the constant. Initial value bumped to
  `"v2"` to absorb today's `has_docstring` / `has_return_type` /
  `has_param_types` detectors.

- **Multi-worker progress UI.** ``absentia check --jobs N`` in
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
  ``absentia est --history`` and turns the previously opaque mining
  tail into a profiling-grade signal.
- **`--cold [PATH]` flag on `absentia check` and `absentia est`.** Dev-
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
    argument (`absentia suppress <gap_id> <path>`) for symmetry with
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
    decision points; opt-in, gated on `ABSENTIA_DEBUG=1` so other
    code can check it without importing CLI internals.

  Use cases: validating extractor changes (no stale cached
  entities), benchmarking the parse stage in isolation, debugging
  suspected cache weirdness without nuking ``.absentia/``.

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



- **Per-stage progress UI.** `absentia check` in interactive text
  mode now emits five ✓ summary lines (walk / parse / store / mine
  / finalize), each with elapsed time, plus live spinners during
  long stages. Mining-stage spinner sub-line surfaces per-strategy
  completion ("3/7 done · last: symmetry pairs · 47 rules so far"),
  turning the previously silent multi-minute mining tail into a
  visible, diagnosable stage. Auto-suppresses on non-TTY.
- **`--max-gaps N` CI tolerance flag.** `absentia check --max-gaps 5`
  exits non-zero only when the gap count exceeds 5. Default
  behavior (no flag) keeps the strict "any gap fails" exit policy.
  Useful for adopting absentia on an existing codebase without
  blocking the build the first day.
- **`absentia --jobs-default N`** — pin the default worker count for
  `absentia check`. Saved to `~/.absentia/settings.json`. If N exceeds
  detected core count, re-prompts to confirm; non-TTY contexts
  refuse without `--yes`. `--jobs-default 0` reverts to auto.
- **`absentia --purge [PATH]` and `absentia --purge-all`.** Top-level
  flags to remove `.absentia/` per-project state and the machine-wide
  cache, with a `[y/N]` prompt + non-TTY refusal.
- **`absentia est --history`** — print the recent `absentia check`
  runs that feed the estimator, plus the aggregated mining
  throughput. Useful for auditing what data the prediction is
  based on.
- **Continuous-calibration runs log** at `~/.absentia/runs.jsonl`.
  Every successful `absentia check` appends a row (timestamp,
  version, cores, jobs, root, language-byte shape, per-stage
  timings). `absentia est` aggregates ≥3 fresh compatible runs into
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
  shows full `absentia check` time at each `--jobs` setting, not
  just parse.
- **Calibrated mining-tail prediction** (`mining_seconds_per_byte`
  in `calibration.json`) so `absentia est` can predict full check
  time before the user has run check even once.
- **Per-stage timings persisted** to `.absentia/last_run.json`
  (`stage_durations_ms` map for walk / parse / store / mine /
  finalize) so the est report can cite real ground truth.

### Changed

- **`absentia init` no longer hardcodes `languages = ["python"]`.**
  The generated `absentia.toml` comments out the line, so omitting
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

(Pre-optimization headline still cited; see the `Performance` block
above for the most recent end-to-end measurements.)

---

## Earlier (rolled into [Unreleased] before first release)

### Added

- **Series-gap detection.** Fourth mining strategy. Detects missing
  numeric indices in same-directory file sequences:
  ``migrations/0001_*.py``, ``0002_*.py``, ``0004_*.py`` →
  ``missing 0003_*.py``. Clusters by sequential proximity (default
  max gap = 5) so a stray ``0099_*`` doesn't create a 96-element
  gap range against an early cluster. The fourth latin-flavored
  category from lacuna_plan; absentia now catches gaps in series,
  not just gaps in patterns.
- **Call-pair detection.** Mines paired-call symmetries within
  function scope: ``9 of 10 functions calling bus.subscribe also
  call bus.unsubscribe`` flags the 10th. Catches project-specific
  resource pairs (subscribe/unsubscribe, audit.begin/audit.commit,
  trace.start/trace.stop, custom acquire/release APIs) that no
  off-the-shelf linter knows about. Conservative defaults
  (min_confidence=0.9, min_support=5) filter out noise from
  language built-ins. Doesn't try control-flow analysis — that's
  linter territory; absentia stays at the project-convention layer.
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
  extractors. Pluggable via the `absentia.extractors` entry-point
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
  watch mode) and `absentia check` for CI batch use.
- **`e` explain modal** in the TUI — peek-style "why was this gap
  flagged" panel that doesn't move you off the gaps list.
- **Parallel parse + extract** across a worker pool. `absentia check
  --jobs N`; default is half of detected CPU cores. Workers spawn
  lazily and skip the pool entirely on small jobs.
- **`absentia est`** — cold-scan time estimator. Walks the corpus,
  applies a calibrated cost model (per-language throughput +
  Amdahl's law), prints a jobs-vs-time table.
- **First-run calibration** with cache at `~/.absentia/calibration.json`.
  Multi-jobs Amdahl `p` fitting, per-language BPS measurement,
  bundled synthetic corpus (`--use-synthetic`), version +
  core-count + 90-day staleness invalidation.
- **Estimate integration points.** Preamble line in `absentia check`
  text mode; first-scan footer in `absentia init`; transient
  "estimating ~Xs" subtitle in the TUI on cold scans.
- **Apache 2.0 license** (see `LICENSE` and `NOTICE`).
- **CI pipeline** — pytest matrix on Python 3.11/3.12/3.13, ruff,
  mypy (lenient), `mkdocs build --strict`.
- **Documentation** via Diátaxis + mkdocs-material:
  - Tutorials: quickstart
  - Reference: CLI, `absentia.toml`, selectors, TUI keybindings
  - Explanation: what-is-negative-space, why-no-llm,
    how-mining-works, architecture-and-performance, the
    cold-scan time estimator

### Performance

Benchmarked on 16 large public corpora totaling ~2.4M entities.
Headline: absentia scans the entire Linux kernel — 666,574 entities
across ~30 million lines of C — in 96.7s on a single Python process
on an M-series MacBook. Full table in
`docs/explanation/architecture.md`.

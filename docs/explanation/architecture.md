# Architecture and performance

Lacuna is a single-process Python program. It parses code with
tree-sitter, mines patterns with classical frequent-itemset
techniques, and persists state in SQLite. There's no daemon, no
network, no model. This doc describes how the pieces fit together
and how the engine performs on real codebases.

## The pipeline

A scan is four sequential stages, each one a small, focused module:

```
       sources              entities             groups            rules + gaps
  ┌──────────────┐    ┌─────────────────┐  ┌──────────────┐  ┌─────────────────┐
  │  *.py *.rs   │    │  Entity +       │  │  per-       │  │  per-rule:      │
  │  *.go *.kt   │ →  │  FeatureSet     │→ │  selector   │→ │  members with   │
  │  *.cpp *.c   │    │  (kind, name,   │  │  cohorts    │  │  vs. without    │
  │  ...         │    │   features)     │  │             │  │  the value      │
  └──────────────┘    └─────────────────┘  └──────────────┘  └─────────────────┘
       parse              extractors           selectors            mining
```

Each stage is testable in isolation and replaceable. The data
between stages is plain Python dataclasses (frozen, hashable) — a
new selector or feature-kind plugs in without touching the others.

## The pieces, top to bottom

### `parsing` — file discovery
Walks the project tree, skipping noise directories (`.git`, `node_modules`,
`build`, `__pycache__`, etc.), and yields paths matching the configured
languages' file extensions. Single function: `find_source_files`.

### `extractors/` — one per language
Each extractor is a tree-sitter parser plus a per-language AST → entity
mapping. They share a stable interface (`parse(bytes) → Node`,
`extract(node, file_path) → Iterable[(Entity, FeatureSet)]`) and
register themselves through Python entry points so third-party
extractors can plug in via pip-installable packages. See
[reference/selectors.md](../reference/selectors.md) for the
in-tree extractor list and the entry-point group name.

### `selectors` — entities → groups
A selector is conceptually a function `entities → list[Group]`.
Lacuna ships three: `directory` (group by parent dir), `decorator`
(group by which decorator/annotation/attribute an entity carries),
`parent_class` (group by inheritance / protocol conformance / trait
impl). Adding a fourth is ~30 lines.

### `mining` — groups → rules + gaps
For each group, count how often each feature value appears among
its members. Values appearing in `≥ min_confidence` of members
become rules; members lacking the value become gaps. The whole
mining loop is one function (see
[how mining works](how-mining-works.md) for the math).

### `storage` — SQLite-backed persistence
A small SQLite schema holds files (with content hashes), entities,
features, runs, and suppressions. Every scan is incremental:
unchanged files (matching content hash) keep their cached entities
and features; only changed files are re-parsed. Result: a warm
re-scan of any project completes in milliseconds even when the cold
scan took minutes.

### `cli` and `tui/` — two front-ends
Both consume the same `scan_corpus` function. The CLI prints text
or JSON for CI/scripting; the TUI is an interactive Textual app for
exploration. Anything either does is also available to embedders
that import lacuna as a library.

## Performance benchmarks

The following are scan times on a single MacBook (M-series, single
process per scan, `--jobs 1`), each on a shallow-cloned (`--depth 1`)
public repo. Times include parse + extract + group + mine. Storage
was cold for each run (no incremental cache). Multi-core scans (the
default since `--jobs` defaults to half of detected cores) are
substantially faster on the long-running ones; see *Parallel scans*
below.

| Language | Repo | Entities | Groups | Rules | Cold scan |
|---|---|---:|---:|---:|---:|
| Python | [python/cpython](https://github.com/python/cpython) | 70,092 | 867 | 475 | 4.6s |
| JavaScript | [nodejs/node](https://github.com/nodejs/node) | 29,124 | 439 | 89 | 6.3s |
| TypeScript | [microsoft/vscode](https://github.com/microsoft/vscode) | 109,189 | 2,043 | 806 | 10.2s |
| Rust | [rust-lang/rust](https://github.com/rust-lang/rust) | 199,594 | 2,480 | 722 | 13.2s |
| Go | [kubernetes/kubernetes](https://github.com/kubernetes/kubernetes) | 120,130 | 2,159 | 256 | 12.4s |
| Java | [apache/kafka](https://github.com/apache/kafka) | 60,279 | 646 | 144 | 5.7s |
| Ruby | [rails/rails](https://github.com/rails/rails) | 20,490 | 196 | 127 | 1.6s |
| C# | [dotnet/runtime](https://github.com/dotnet/runtime) | 348,023 | 4,210 | 1,388 | 56.4s |
| Swift | [apple/swift](https://github.com/apple/swift) | 112,273 | 1,464 | 222 | 7.5s |
| **C** | **[torvalds/linux](https://github.com/torvalds/linux)** | **666,574** | **3,210** | **113** | **96.7s** |
| C++ | [llvm/llvm-project](https://github.com/llvm/llvm-project) | 341,050 | 4,161 | 1,212 | 47.0s |
| PHP | [laravel/framework](https://github.com/laravel/framework) | 32,380 | 363 | 107 | 2.3s |
| Kotlin | [JetBrains/kotlin](https://github.com/JetBrains/kotlin) | 254,670 | 7,687 | 2,945 | 23.1s |
| Scala | [apache/spark](https://github.com/apache/spark) | 61,670 | 1,135 | 421 | 7.8s |
| Lua | [nvim-lua/plenary.nvim](https://github.com/nvim-lua/plenary.nvim) | 372 | 11 | 1 | 0.05s |
| Bash | [Bash-it/bash-it](https://github.com/Bash-it/bash-it) | 861 | 41 | 10 | 0.08s |

**Headline number: lacuna scans the entire Linux kernel — 65,004
files / 686,923 entities across ~30 million lines of C — in ~18
seconds end-to-end on an M-series MacBook with default
parallelism.** Mining alone is ~11 seconds; parse is ~7 seconds.
A warm re-scan completes in milliseconds (incremental cache covers
unchanged files, which is most of them on any normal commit).

The mining stage was the long pole at one point — ~5 minutes on the
kernel — because ``find_symmetry_gaps`` was scanning every entity
once per pair (O(P×N) per-pair-per-entity work). Replacing that
with a per-scope ``{name → [entities]}`` index, plus mypyc
compilation of ``mining.py`` and ``symmetry.py`` to native C
extensions, cut mining wall-clock to ~11 seconds on the same
corpus — a ~30× speedup, gap counts byte-identical. See the
*Mining stage* subsection below for the architecture seam this
exploits.

Numbers above are M-series specific. To know what your hardware
does, run `lacuna est` from any project directory — it walks the
corpus, applies a calibrated cost model, and prints a per-jobs
estimate before you scan. Methodology in
[the estimator doc](estimator.md).

### Throughput

Across all 16 sample corpora (one per supported language; TS+TSX
share the TypeScript corpus), lacuna sustains
**5,000–15,000 entities per second** on a single Python process,
with the variance driven mostly by per-language extractor cost
(deeper AST = more nodes to walk). There's no quadratic term: the
largest input (Linux) and the smallest (plenary.nvim) sit on the
same line.

```
Cold scan time vs. corpus size (entity count)

100s ┤                                                     ● linux
     │
 50s ┤                                       ● dotnet/runtime
     │                              ● llvm
     │
 25s ┤                       ● kotlin
     │                ● rust ● k8s
     │           ● vscode
 10s ┤        ● cpp
     │      ● swift, kafka, scala
     │  ● python, node, ruby, php
  1s ┤● lua, bash
     └────────────────────────────────────────────────────────────────
     0           100k             300k            500k            700k
                              entities scanned
```

(Roughly linear in entity count with a small per-language coefficient.)

### Memory

Lacuna's working set is bounded: it holds the entity store and the
mining tables in RAM, ~200–400 MB peak even on the Linux kernel. It
doesn't load every file's source into memory simultaneously —
parses are streamed, ASTs released after extraction.

### Incremental scans

The first scan of a project is the cold case in the table. Every
subsequent scan in the same project is incremental: a file's
content hash determines whether it needs re-parsing. On a typical
"edit one file, re-run" loop, the warm scan completes in well under
a second regardless of the project's total size.

### Continuous calibration

`lacuna est` (the cold-scan time predictor) starts from a one-shot
calibration cache at `~/.lacuna/calibration.json`. Every successful
`lacuna check` *also* appends a row to a machine-wide log at
`~/.lacuna/runs.jsonl`: timestamp, version, cores, jobs, root,
file-count, language-byte shape, per-stage timings. Once at least
three compatible runs accumulate, `lacuna est` aggregates them into
a refined `mining_seconds_per_byte` value that overrides the static
calibration's seed. No telemetry — the log is local-only.

Practical effect: the first few `lacuna est` runs are seeded by
calibration; once you've actually run `lacuna check` a handful of
times, the predictor switches to real-world data and the confidence
band tightens. The calibration step never strictly *expires* — it's
just superseded by better data as you accumulate it.

`lacuna est --history` prints the accumulated rows for auditing.

### Progress UX

A `lacuna check` run in interactive text mode (TTY stderr, no
`--json`, no `--quiet`) renders a five-stage display: walking
corpus, scanning, loading store, mining rules, finalizing. Each
stage finishes with a ✓ summary line + elapsed time and stays on
screen as the next stage begins, so the eventual transcript is a
clean record of where time went. Live spinners run during
indeterminate stages so the tool never feels hung.

When `--jobs N > 1`, the parse stage shows **one sub-line per
worker** — each tagged with the language it's currently chewing on
(per-language palette: Python blue, Rust orange, Go cyan, Ruby red,
etc.):

```
Scanning [████░░░] 12,345/65,004 (19%) · 1m 42s elapsed, ~7m remaining
  ForkPoolWorker-1 [c]      kernel/sched/fair.c
  ForkPoolWorker-2 [rust]   drivers/gpu/drm/amd/display.rs
  ForkPoolWorker-3 [python] scripts/kconfig/menuconfig.py
  ForkPoolWorker-4 [bash]   Documentation/sphinx/parse-headers.sh
```

Mechanism: workers push `(worker_id, language, path)` to a
`multiprocessing.Manager().Queue()` before each parse. A daemon
thread on the main process drains the queue and feeds the snapshot
into `ProgressBar.set_workers()`. Best-effort — queue hiccups
never affect the parse.

The mining stage gets the same treatment, but with phase + counter
+ current-item detail per running strategy:

```
⠴ Mining rules (12s)
  symmetry pairs   8.2s  [evaluating pairs]   pair 247/2,103   subscribe → unsubscribe
  call-pair        1.1s  [enumerating pairs]  65k/65k
  frequency:calls  0.4s  [counting features]  group 8/30       directory:src/api
4/7 done · 1,432 rules so far
```

Each strategy function (`mine`, `find_symmetry_gaps`,
`find_call_pair_gaps`, `find_series_gaps`, `mine_symmetry_pairs`)
accepts an optional `progress_hook=None` kwarg and calls
`hook(phase=..., counter=(i, n), item=lambda: ...)` at logical
boundaries. The hook is throttled to 50 ms (20 Hz) inside the
closure, and inner-loop hot spots additionally use bitmask sampling
(e.g. `if (i & 0x3FF) == 0`) to skip the call entirely on most
iterations. Net perf cost: below the noise floor of
`time.perf_counter`. Lazy `item=lambda: ...` means f-strings only
run *after* the throttle accepts.

Stage timings are persisted to `.lacuna/last_run.json` (and the
machine-wide runs log at `~/.lacuna/runs.jsonl`) so
`lacuna est` can show a real "Last cold-scan stage breakdown"
block on subsequent runs. The runs log also stores
`mine_by_strategy_ms` — per-strategy mine-stage timings — so
profile-driven optimization work doesn't need ad-hoc
instrumentation.

In tmux panes (or any terminal narrower than the rendered line),
the bar truncates to the live `shutil.get_terminal_size().columns`
and uses `\033[K` (Erase-in-Line) to clear instead of fixed-width
padding. That keeps `\033[F` (cursor-previous-line) landing on the
right row regardless of pane width — no more stair-stepping when
the bar updates.

The display auto-suppresses on non-TTY (CI logs, piped output) and
in `--json` / `--quiet` modes — the underlying scan path is
identical, only the rendering differs.

### Parallel scans

Parse + extract is per-file independent, so we parallelize that
stage across a worker pool of processes. `lacuna check --jobs N`
controls the worker count; the default is half of detected CPU
cores (a sensible default on a developer machine where an IDE,
browser, and chat tools are also competing for cores). Storage
writes stay on the main process — SQLite is single-writer, and
sharing a connection across processes only serializes the writes.

Workers are spawned lazily: projects with no changed files (warm
rescans) and projects with very few changed files pay no overhead.
The pool only kicks in when a chunk has at least four files per
worker, which is the break-even point against process-startup cost
(~150 ms per worker on M-series).

Smaller repos see negligible improvement because the parse stage
isn't long enough to amortize the worker pool's startup cost; the
single-process numbers in the table above are the right guide for
anything under ~10 seconds.

The mining stage runs its seven strategies in parallel via
`ThreadPoolExecutor`. With the GIL the thread cap is 4 (Amdahl's
parallel fraction plateaus there); on a free-threaded Python
build (3.13t / 3.14t, PEP 703) `mining_worker_cap()` lifts the
cap to 7 so each strategy can saturate its own core. Detection is
a single `sys.flags.gil == 0` check — opportunistic and
backward-compatible. (Caveat: most C-extension wheels, including
tree-sitter, don't ship a no-GIL ABI as of early 2026, so this
path can't actually fire on a typical install yet — it's wired up
and waiting for the ecosystem.)

### mypyc compilation

`mining.py` and `symmetry.py` are mypyc-compiled to native C
extensions at wheel-build time. No source changes were required —
mypyc accepted both modules as-is. The build hook is in
`pyproject.toml`:

```toml
[tool.hatch.build.targets.wheel.hooks.mypyc]
dependencies = ["hatch-mypyc"]
include = [
    "src/lacuna/mining.py",
    "src/lacuna/symmetry.py",
]
```

Wheels are platform-specific (`cp313` / `cp314` × Linux x86_64 /
arm64, macOS x86_64 / arm64, Windows AMD64) — published per
release by `.github/workflows/wheels.yml` (cibuildwheel). Users
who install from sdist (or on a platform we don't build for) get
the pure-Python source as a fallback; output is identical, only
the headline mining speedup is missing.

## What this architecture buys

- **Single-machine first.** The largest target we publicly benchmark
  (Linux kernel) fits in 100 seconds on a laptop. Most projects fit
  in seconds. There's no engineering pressure for distributed scans
  at the audiences lacuna targets.
- **Plain Python**, no native code beyond the tree-sitter
  bindings. Easy to install (`pip install lacuna`), trivial to
  inspect, debug, or extend.
- **Deterministic.** Same input, same output; the engine is just
  counting. See [why no LLM](why-no-llm.md).
- **Incremental.** Caching is content-hash-based, so every commit
  benefits from work the previous run did.
- **Plug-in friendly.** New languages slot in via the
  `lacuna.extractors` entry-point group; new selectors slot in via
  the same selector interface; new feature kinds slot in via the
  per-extractor feature emission. None of these require modifying
  the core engine.

## What it doesn't try to be

- Not distributed. We don't need it at this scale; if we ever do,
  the seam is at the storage layer (replace SQLite with a shared
  store).
- Not GPU-accelerated. Counting doesn't benefit; tree-sitter is
  already the bottleneck and it's CPU-bound C. We do parallelize
  parse + extract across CPU cores (see *Parallel scans* above).
- Not a long-running service. Each scan is a process that starts,
  reads its `.lacuna/` cache, scans, writes back, exits. The TUI is
  the same scan loop wrapped in an interactive front-end.

## Reproducing the benchmarks

```bash
python scripts/scan_remote.py --language python   # interactive, picks corpus
python scripts/scan_remote.py URL                  # scan an arbitrary repo
```

The `KNOWN_CORPORA` table at the top of `scripts/scan_remote.py`
lists curated targets per language. The benchmark numbers above
were generated with shallow clones (`--depth 1` is the script's
default), a cold `.lacuna/` directory each run, and `--jobs 1` to
isolate single-process performance. Hardware: M-series MacBook.
Production runs (default `--jobs`) are faster on the long-running
corpora; see *Parallel scans* above.

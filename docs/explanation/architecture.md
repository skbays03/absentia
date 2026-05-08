# Architecture and performance

Absentia is a single-process Python program. It parses code with
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

The four stages above describe the *algorithm*. The runtime
progress display shows **five stages** — `walk → parse → store →
mine → finalize` — adding a file-discovery preamble and an
output/persistence postamble around the conceptual core. The
conceptual *group* and *compare* steps fold into the *mine*
stage's progress line. See [Progress UX](#progress-ux) below.

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
Absentia ships three: `directory` (group by parent dir), `decorator`
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
re-scan of a small edited subset completes in fractions of a second
regardless of the project's total size; even a kernel-scale rescan
with no file changes runs in tens of seconds because mining + storage
still walk every cached entity.

### `cli` and `tui/` — two front-ends
Both consume the same `scan_corpus` function. The CLI prints text
or JSON for CI/scripting; the TUI is an interactive Textual app for
exploration. Anything either does is also available to embedders
that import absentia as a library.

## Performance benchmarks

The following are `--jobs 1` cold-scan times on smoke-test-sized
public repos — the per-language sanity-check corpora that
`scripts/scan_remote.py` knows about (`KNOWN_CORPORA`'s first
entry per language). They show absentia's per-language baseline
on small-to-medium codebases; the *Headline numbers* section
below covers the big-corpus case study (Linux kernel, 65 k files
/ 687 k entities). Storage was cold for each run (no incremental
cache).

| Language | Corpus | Files | Entities | Rules | Gaps | Cold scan |
|---|---|---:|---:|---:|---:|---:|
| Python | [pallets/flask](https://github.com/pallets/flask) | 84 | 893 | 45 | 16 | 0.22s |
| JavaScript | [expressjs/express](https://github.com/expressjs/express) | 141 | 55 | 1 | 0 | 0.15s |
| TypeScript | [nestjs/nest](https://github.com/nestjs/nest) | 1,718 | 4,066 | 310 | 121 | 0.38s |
| Rust | [BurntSushi/ripgrep](https://github.com/BurntSushi/ripgrep) | 103 | 2,694 | 60 | 291 | 0.27s |
| Go | [urfave/cli](https://github.com/urfave/cli) | 66 | 914 | 24 | 35 | 0.18s |
| Java | [google/gson](https://github.com/google/gson) | 262 | 2,509 | 84 | 280 | 0.30s |
| Ruby | [sinatra/sinatra](https://github.com/sinatra/sinatra) | 147 | 204 | 0 | 0 | 0.17s |
| C# | [serilog/serilog](https://github.com/serilog/serilog) | 214 | 985 | 51 | 43 | 0.22s |
| C++ | [nlohmann/json](https://github.com/nlohmann/json) | 491 | 1,856 | 144 | 197 | 0.78s |
| PHP | [slimphp/Slim](https://github.com/slimphp/Slim) | 125 | 852 | 73 | 90 | 0.18s |
| Kotlin | [Kotlin/kotlinx.coroutines](https://github.com/Kotlin/kotlinx.coroutines) | 1,106 | 7,277 | 369 | 345 | 0.67s |
| Scala | [playframework/playframework](https://github.com/playframework/playframework) | 1,526 | 8,556 | 418 | 366 | 0.98s |
| Lua | [nvim-lua/plenary.nvim](https://github.com/nvim-lua/plenary.nvim) | 114 | 373 | 2 | 3 | 0.16s |
| Bash | [Bash-it/bash-it](https://github.com/Bash-it/bash-it) | 336 | 861 | 16 | 28 | 0.18s |
| Swift | [Alamofire/Alamofire](https://github.com/Alamofire/Alamofire) | 108 | 1,656 | 94 | 204 | 0.33s |

> *Measured 2026-05-07 on a 10-core M-series MacBook at commit
> `a48c4c7`, jobs=1, shallow-cloned (`--depth 1`) via*
> `scripts/scan_remote.py`. *Re-running on different hardware will
> shift wall-clock proportionally; `absentia est` calibrates per
> machine.*

**Headline numbers** (absentia against the Linux kernel — 65,004 files
/ 686,923 entities across ~30 million lines of C, on a 10-core
M-series MacBook):

| Mode | End-to-end at default jobs (5) | Single-process (--jobs 1) |
|---|---:|---:|
| **Warm** (cache primed, 0 files changed) | ~28 s | ~26 s |
| **Cold** (cache empty, full re-parse) | ~50 s | ~95 s |

Warm-scan stage breakdown: parse ~8 s (cache hits) + mine ~14 s +
store ~3 s + finalize ~0 s.
Cold-scan stage breakdown: parse ~27 s + mine ~14 s + store ~2 s
+ finalize ~0 s.

A warm rescan with a small edited subset (typical edit-test loop)
runs in fractions of a second — only changed files re-parse. The
"warm" numbers above are the worst-case warm scan: every file unchanged
(no parse work) but mining + storage still touch every cached entity.

The mining stage was the long pole at one point — ~5 minutes on the
kernel — because ``find_symmetry_gaps`` was scanning every entity
once per pair (O(P×N) per-pair-per-entity work). Replacing that
with a per-scope ``{name → [entities]}`` index, plus mypyc
compilation of ``mining.py`` and ``symmetry.py`` to native C
extensions, cut mining wall-clock to ~14 seconds on the same
corpus — a ~23× speedup, gap counts byte-identical to the pre-
optimization baseline. See the *Mining stage* subsection below for
the architecture seam this exploits.

> *Numbers above measured 2026-05-07 on commit `a48c4c7` against
> a clean Linux kernel checkout. To know what your hardware does,
> run* `absentia est` *— it walks the corpus, applies a calibrated
> cost model, and prints a per-jobs estimate before you scan.*

Numbers above are M-series specific. To know what your hardware
does, run `absentia est` from any project directory — it walks the
corpus, applies a calibrated cost model, and prints a per-jobs
estimate before you scan. Methodology in
[the estimator doc](estimator.md).

### Throughput

Across all 16 sample corpora (one per supported language; TS+TSX
share the TypeScript corpus), absentia sustains
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

Absentia's working set scales with entity count. Peak RSS during a cold
kernel scan (686k entities) is ~2 GB at single-process and ~2.2 GB
at default jobs (the 5 worker processes hold tree-sitter ASTs
simultaneously during parse). Most projects sit far below that —
a 30k-entity Python codebase peaks around 200 MB; a 100k-entity
Rust project around 500 MB. Absentia doesn't load every file's source
into memory simultaneously — parses are streamed and ASTs released
after extraction; the steady-state memory after extract is the entity
store + mining indexes.

### Incremental scans

The first scan of a project is the cold case in the table. Every
subsequent scan in the same project is incremental: a file's
content hash determines whether it needs re-parsing. On a typical
"edit one file, re-run" loop, the warm scan completes in well under
a second on small-to-medium projects — only the changed file
re-parses, and mining over the cached entity store is fast.

A rescan with **no** file changes is the floor case: it skips the
parse stage entirely (every file's content hash matches), but
mining + storage still touch every cached entity. On a kernel-scale
corpus that floor is tens of seconds (~28 s at default jobs); on
medium projects it's sub-second. Per-project warm-scan time scales
roughly linearly in entity count.

The cache is salted with `extractors.EXTRACTOR_FINGERPRINT` so a
release shipping new feature_kinds or extractor changes invalidates
the affected entries automatically — users don't have to know to
`--cold` after upgrading. See `CONTRIBUTING.md` §8 for the bump
policy.

### Continuous calibration

`absentia est` (the cold-scan time predictor) starts from a one-shot
calibration cache at `~/.absentia/calibration.json`. Every successful
`absentia check` *also* appends a row to a machine-wide log at
`~/.absentia/runs.jsonl`: timestamp, version, cores, jobs, root,
file-count, language-byte shape, per-stage timings. Once at least
three compatible runs accumulate, `absentia est` aggregates them into
a refined `mining_seconds_per_byte` value that overrides the static
calibration's seed. No telemetry — the log is local-only.

Practical effect: the first few `absentia est` runs are seeded by
calibration; once you've actually run `absentia check` a handful of
times, the predictor switches to real-world data and the confidence
band tightens. The calibration step never strictly *expires* — it's
just superseded by better data as you accumulate it.

`absentia est --history` prints the accumulated rows for auditing.

### Progress UX

A `absentia check` run in interactive text mode (TTY stderr, no
`--json`, no `--quiet`) renders a five-stage display: walking
corpus, scanning, loading store, mining rules, finalizing. (These
are the runtime stages — `walk → parse → store → mine → finalize`
— that bookend the four conceptual stages from
[*The pipeline*](#the-pipeline) above.) Each stage finishes with a
✓ summary line + elapsed time and stays on screen as the next
stage begins, so the eventual transcript is a clean record of
where time went. Live spinners run during indeterminate stages so
the tool never feels hung.

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

Stage timings are persisted to `.absentia/last_run.json` (and the
machine-wide runs log at `~/.absentia/runs.jsonl`) so
`absentia est` can show a real "Last cold-scan stage breakdown"
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
stage across a worker pool of processes. `absentia check --jobs N`
controls the worker count; the default is half of detected CPU
cores (a sensible default on a developer machine where an IDE,
browser, and chat tools are also competing for cores). Storage
writes stay on the main process — SQLite is single-writer, and
sharing a connection across processes only serializes the writes.

Workers are spawned lazily: projects with no changed files (warm
rescans) and projects with very few changed files pay no overhead.
The pool only kicks in when a chunk has at least four files per
worker, which is the break-even point against process-startup cost
(~60 ms per worker on M-series).

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
    "src/absentia/mining.py",
    "src/absentia/symmetry.py",
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
  at the audiences absentia targets.
- **Plain Python**, no native code beyond the tree-sitter
  bindings. Easy to install (`pip install absentia`), trivial to
  inspect, debug, or extend.
- **Deterministic.** Same input, same output; the engine is just
  counting. See [why no LLM](why-no-llm.md).
- **Incremental.** Caching is content-hash-based, so every commit
  benefits from work the previous run did.
- **Plug-in friendly.** New languages slot in via the
  `absentia.extractors` entry-point group; new selectors slot in via
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
  reads its `.absentia/` cache, scans, writes back, exits. The TUI is
  the same scan loop wrapped in an interactive front-end.

## Reproducing the benchmarks

```bash
python scripts/scan_remote.py --language python   # interactive, picks corpus
python scripts/scan_remote.py URL                  # scan an arbitrary repo
```

The `KNOWN_CORPORA` table at the top of `scripts/scan_remote.py`
lists curated targets per language. The benchmark numbers above
were generated with shallow clones (`--depth 1` is the script's
default), a cold `.absentia/` directory each run, and `--jobs 1` to
isolate single-process performance. Hardware: M-series MacBook.
Production runs (default `--jobs`) are faster on the long-running
corpora; see *Parallel scans* above.

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

**Headline number: lacuna scans the entire Linux kernel — 666,574
entities across ~30 million lines of C — in 96.7 seconds.** A warm
re-scan of any of these completes in milliseconds (incremental cache
covers unchanged files, which is most of them on any normal
commit).

### Throughput

Across all 16 corpora, lacuna sustains **5,000–15,000 entities per
second** on a single Python process, with the variance driven mostly
by per-language extractor cost (deeper AST = more nodes to walk).
There's no quadratic term: the largest input (Linux) and the
smallest (plenary.nvim) sit on the same line.

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
anything under ~10 seconds. Speedup tops out around 4× even on a
beefier machine — the serial tail (group + mine + storage) becomes
the bottleneck per Amdahl. On an 8-core M-series at the default
half-cores, expect roughly 3–3.5× on the long-running corpora
(Linux, dotnet/runtime, llvm-project, kotlin).

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

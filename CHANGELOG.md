# Changelog

All notable changes to lacuna will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **16-language extractor system** built on tree-sitter: Python,
  JavaScript, TypeScript, Rust, Go, Java, Ruby, C#, Swift, C, C++,
  PHP, Kotlin, Scala, Lua, Bash. Pluggable via the
  `lacuna.extractors` entry-point group.
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

# Changelog

All notable changes to lacuna will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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

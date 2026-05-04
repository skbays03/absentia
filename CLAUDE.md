# CLAUDE.md — guidance for AI assistants in this repo

## What lacuna is

A code-hygiene tool that mines patterns a codebase already follows and surfaces
places that don't follow them. **No LLM in the engine** — purely classical
(tree-sitter + frequent itemset mining + statistics). User-facing tagline:
*"Find the holes your code already drew."*

See `README.md` for the public-facing pitch.

## Architecture in one screen

Four-layer storage model:

```
User layer       suppressions, annotations, config       sticky across runs
Pattern layer    groups, rules, gaps                     derived per run
Entity layer     entities, features, relations           incremental on file change
Raw layer        files, content hashes, parse cache      incremental on disk change
```

Two consumers of the engine library:

- **TUI** — `lacuna` (default invocation), Textual-based, primary UX
- **Batch CLI** — `lacuna check` for CI, scripts, editor integrations

A future third consumer: a Dev-Dashboard panel that imports the engine as a
Python library or shells out to `lacuna check --json`.

## Locked-in decisions

These were debated and chosen deliberately. Don't reverse them silently — if
a reversal seems warranted, surface the reasoning explicitly.

1. **No LLM in the engine.** Determinism, free explanations as a byproduct of
   rule mining, sub-second feedback, and differentiation from saturated AI
   tooling. Embeddings are reserved for an eventual personal-knowledge variant
   if it materializes; LLM only as an optional natural-language query shell,
   never the core.
2. **TUI is the primary UX.** Built with Textual. Batch CLI is the secondary
   scriptable mode. Number keys for view switching; lowercase for actions.
3. **Standalone repo, not a Dev-Dashboard panel.** Lives at
   `Transcending-Binary/projects/lacuna/`. Designed for Dev-Dashboard (or any
   other host) to embed via library import or `--json` shellout.
4. **Python + SQLite for the MVP.** Rust + alternate stores reserved for the
   enterprise tier. Don't pre-build that infrastructure.
5. **Stable IDs everywhere.** Entities, rules, gaps, and groups all have
   deterministic IDs derived from their identity (not sequence numbers), so
   suppressions persist across rebuilds.
6. **Architectural seams designed for worst-case; implementation built for
   current case.** Storage interface, extractor plugin shape, group selector
   polymorphism, content-hash-driven incremental — all baked in from day 1.
   Rust port, columnar store, parallelism, etc. are deferred until needed.

## Repo layout

```
src/lacuna/        Engine package
tests/             Unit + integration tests
docs/              Mkdocs-material site (Diátaxis structure)
  tutorial/        Learn-by-doing
  how-to/          Task-oriented recipes
  reference/       Look-up authoritative
  explanation/     Concepts + ADRs (in decisions/)
pyproject.toml     Package config
lacuna.toml.example   Sample per-project config
README.md          Public-facing pitch
DEFERRALS.md       Publication-blocking items intentionally deferred
CHANGELOG.md       Per-release notes
```

In **user projects** (not this repo):
- `lacuna.toml` — committed per-project config + project-wide suppressions
- `.lacuna/` — gitignored runtime state directory (entity DB, parse cache, etc.)

## Conventions

- Docs live in this repo. PRs that need docs are caught in review.
- ADRs go in `docs/explanation/decisions/` and are written **when the decision
  is made**, not retroactively.
- Tutorial code blocks must be runnable and tested in CI.
- Commit messages: short imperative subject; body explains *why*, not *what*.
- Deferred publication-blockers go in `DEFERRALS.md`.
- Resolved deferrals get struck through (not deleted) and moved to the
  Resolved section at the bottom of `DEFERRALS.md`.

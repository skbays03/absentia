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

## Dev scripts

`scripts/update_ts.py` discovers every installed `tree-sitter*` package and
checks pip for updates. Run periodically as new lacuna extractors are added —
keeps grammars current without hardcoding the list.

Three modes:

- **Interactive** (default, when run from a TTY): numbered list + menu.
  Choices: 1) apply outdated · 2) apply all · 3) apply specific packages
  by number (e.g. `3 2,4`) · q) quit.
- **Non-interactive apply** (`--apply`, `--apply --all`): runs the upgrade
  without prompting, suitable for CI / cron.
- **Non-interactive info** (`--dry-run`, or non-TTY stdin): prints the
  status and exits.

```bash
python scripts/update_ts.py            # interactive
python scripts/update_ts.py --apply    # upgrade outdated, no prompt
python scripts/update_ts.py --apply --all   # upgrade everything, no prompt
python scripts/update_ts.py --dry-run  # print status only
```

The script is *deliberately* dynamic: it discovers tree-sitter packages by
name prefix rather than reading from a hardcoded list or `pyproject.toml`.
Adding a new language means installing its grammar; the script picks it up
on the next run.

### `scripts/scan_remote.py` — sanity-check against real codebases

Clones a public repo into a temp dir, runs `lacuna check`, and cleans up.
The default is `--depth 1` shallow clone, so even large repos use modest
disk space. Use it to verify a freshly added extractor actually works on
real-world code.

```bash
python scripts/scan_remote.py --list                       # show known corpora
python scripts/scan_remote.py --language python            # pick a Python corpus
python scripts/scan_remote.py URL                          # scan an arbitrary URL
python scripts/scan_remote.py URL --keep                   # leave the clone in place
python scripts/scan_remote.py URL --languages python,go    # restrict the scan
```

**Convention: when adding a new language extractor, add at least one entry
to `KNOWN_CORPORA` in `scripts/scan_remote.py`.** Pick a public repo that's
idiomatic for the language, small-to-medium sized, convention-rich, and
well-maintained. The `KNOWN_CORPORA` dict is *the* sanity-check resource —
if a language ships without an entry, we have no quick way to verify the
extractor works on real code as the codebase evolves.

## Conventions

- Docs live in this repo. PRs that need docs are caught in review.
- ADRs go in `docs/explanation/decisions/` and are written **when the decision
  is made**, not retroactively.
- Tutorial code blocks must be runnable and tested in CI.
- Deferred publication-blockers go in `DEFERRALS.md`.
- Resolved deferrals get struck through (not deleted) and moved to the
  Resolved section at the bottom of `DEFERRALS.md`.

## Commit format

Enforced by `.githooks/commit-msg` (Python). Rules:

1. Subject ≤72 chars, imperative mood (the hook only checks length; mood is
   on you).
2. Blank line between subject and body if body exists.
3. Body lines ≤100 chars.
4. **Every commit must include an `Authored-by:` trailer** identifying the
   human author. AI-assisted commits add `Co-Authored-By:` for the assistant.

Example:

```
Add foo to bar

Body explaining the *why* of the change. Body lines are wrapped at
100 chars to match the ruff line-length.

Authored-by: Shawn Bays <shawnbays2003@gmail.com>
Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

### Enabling the hook in a fresh clone

The hook is tracked in `.githooks/`. Wire it up with:

```bash
ln -sf ../../.githooks/commit-msg .git/hooks/commit-msg
```

(Or globally for all hooks: `git config core.hooksPath .githooks`.)

# Contributing to lacuna

Conventions established through working on the project. Follow them
when adding code; depart from them only with explicit justification.

For project context (what lacuna is, locked-in design decisions,
repo layout, dev scripts), see [CLAUDE.md](CLAUDE.md).

---

## 1. Progress UI for waiting operations

Anything that can run for more than ~1 second from a human's
perspective gets a progress indicator. Three flavors live in
`src/lacuna/progress.py`:

| Use this | When |
|---|---|
| `ProgressBar(total=N, label="…")` | The work iterates over a known total — a per-file scan loop. Renders a percentage + ETA. |
| `StepIndicator(total_steps=N, prefix="[…]")` + `ticking()` context manager | A sequence of opaque sub-tasks where each is a black box (calibration's per-jobs and per-language scans). Renders `[idx/N] label… elapsed`. |
| `Spinner(label="…")` + `spinning()` context manager | Indeterminate work (tree walks, `rglob` over `$HOME`). Animated frame + elapsed time. |

Conventions:

- All three auto-skip on non-TTY (CI logs and piped output stay
  clean). Don't gate on TTY yourself — the progress class handles
  it.
- All three throttle redraws to ~10 Hz internally; callers can
  call `update`/`tick` once per item without flooding.
- All three write to **stderr**, so a piped stdout still gets
  progress visible to the human.
- Wrap a progress callback in `try/except` at the call site —
  progress UI must never break the underlying operation.

If you're tempted to do a multi-second silent operation, you're
probably wrong.

## 2. Commit messages

Enforced by `.githooks/commit-msg`. Rules:

1. Subject ≤ 72 characters, imperative mood.
2. Blank line between subject and body when a body exists.
3. Body lines ≤ 100 characters (matches ruff line-length).
4. **`Authored-by:` trailer required** identifying the human author.
5. **`Co-Authored-By:`** added when the commit was AI-assisted.
6. One commit per logical change. Don't amend published commits.

Example:

```
Add foo to bar

Body explaining the *why* of the change. Body lines are wrapped at
100 chars to match the ruff line-length.

Authored-by: Shawn Bays <shawnbays2003@gmail.com>
Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

Wire the hook into a fresh clone:

```bash
ln -sf ../../.githooks/commit-msg .git/hooks/commit-msg
# or globally (also picks up pre-push, see §6):
git config core.hooksPath .githooks
```

There's also a **`pre-push` hook** at `.githooks/pre-push` that runs
`scripts/local_ci.sh` (the same checks GitHub Actions runs: ruff,
mypy, pytest + coverage gate, mkdocs --strict). The
`git config core.hooksPath .githooks` install picks both up. See §6
*Local CI before commit / push* for the manual invocation form and
the skip-once escape hatch.

## 3. Destructive operations

Any flag or subcommand that deletes data follows the
`--purge` / `--purge-all` precedent:

1. **Print a disclaimer** *before* the prompt, listing exactly
   what will be deleted and what will be preserved.
2. **Default `[y/N]`** — capital N, so a bare Enter aborts.
3. **Refuse outright in non-TTY contexts** unless the user passed
   `--yes` / `-y` to skip the prompt explicitly.
4. **Sanity-check the target** before deleting. If a `.lacuna/`
   directory doesn't have the expected `version` or `state.db`,
   refuse — it might be an unrelated user file.
5. Report per-deletion failures without aborting the rest of the
   batch.

Tests pass `confirm=False` to skip the prompt; never wire tests to
piping `y` through stdin.

## 4. Local CI before commit / push

Two ways to run the same checks GitHub Actions runs
(`.github/workflows/ci.yml`):

```bash
# One-shot script — orders cheapest-first so iteration is fast.
bash scripts/local_ci.sh

# Or the four commands manually:
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy src/lacuna
.venv/bin/python -m pytest --cov --cov-report=term-missing -q
.venv/bin/python -m mkdocs build --strict
```

The pre-push hook at `.githooks/pre-push` runs `scripts/local_ci.sh`
automatically before every `git push`, so CI failures are caught
locally before they hit the remote. Install once via
`git config core.hooksPath .githooks` (see §2 — same install picks
up commit-msg and pre-push together).

Skip the pre-push hook for a single push when you genuinely need to:

```bash
git push --no-verify       # skips all client-side hooks
bash scripts/local_ci.sh --skip   # ad-hoc no-op (rarely useful)
```

But expect the next push (or rebase) to bounce on whatever CI
catches — only skip when you have a specific reason.

## 5. Editable install for development

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .[dev,docs]
```

`pip install lacuna` doesn't work — the package isn't on PyPI yet.
For everyday changes, `git pull` is enough; the editable install
points at the working tree. Re-run `pip install -e .` only when
`pyproject.toml` adds a new dependency.

## 6. Doc-with-feature

When you ship a feature, update related docs **in the same commit**:

| Touched | Doc to update |
|---|---|
| New CLI flag or subcommand | `docs/reference/cli.md` + `--help` epilog |
| New config option | `docs/reference/lacuna-toml.md` + `lacuna.toml.example` |
| New TUI keybinding | `docs/reference/tui-keys.md` + in-app `?` help |
| New selector | `docs/reference/selectors.md` |
| Architectural shift | `docs/explanation/architecture.md` |
| User-visible change | `CHANGELOG.md` `[Unreleased]` section |

PRs that move code without the matching doc update get caught in
review.

## 7. Defensive UI hooks

User-supplied callbacks (progress callbacks, custom editor commands,
`on_open_editor` injections) must never break the underlying
operation. Wrap each invocation:

```python
if callback is not None:
    try:
        callback(*args)
    except Exception:
        pass  # UI hook must not break the work
```

The work is the contract; the UI is decoration.

## 8. Bump `EXTRACTOR_FINGERPRINT` when extractor output changes

The per-file content hash that drives lacuna's incremental cache is
salted with `extractors.EXTRACTOR_FINGERPRINT` (a string constant
in `src/lacuna/extractors/__init__.py`). Bumping the constant
invalidates every cached entry on the next scan, so users
automatically pick up new feature_kinds / entity kinds /
extractor-logic-fixes without having to know to `--cold` or
`--purge`.

Bump it whenever extractor *output* changes. Examples that DO need
a bump:
- new `feature_kind` in any extractor's FeatureSet
- new entity kind emitted by any extractor
- bug fix in extractor logic that changes the entity / feature shape
- new built-in extractor language

Examples that DON'T need a bump:
- comment / docstring changes inside extractor source
- pure refactor (e.g. extracting a helper) with no output change
- typo fixes
- changes to `src/lacuna/mining.py`, `selectors.py`, `series.py`,
  or anything *not* under `src/lacuna/extractors/` (mining runs
  from scratch every scan and doesn't read the cache)

CI gate: `scripts/check_fingerprint_bump.sh` runs in CI and fails
when any file under `src/lacuna/extractors/` changed in a PR but
`EXTRACTOR_FINGERPRINT` didn't. The gate has a refactor escape
hatch — include the literal `[no-fingerprint-bump]` in any commit
message in the diff range and the check skips, recording the
exemption in git history.

The bump-history docstring on the constant itself is the authoritative
log of what each version absorbed. Keep the bump in the same commit
as the extractor change; reviewers can see both at once.

## 9. Stable IDs across runs

Suppressions, follow-links, watchers, and external integrations
all depend on stable IDs.

- **Entities** are identified by their qualified name (e.g.
  `src/api/users.py::delete_user`), not by sequence numbers or
  database rowids.
- **Gaps** get a hash-derived short ID (`g-7c91234`) computed
  from `(rule_id, entity_id)`.
- **Rules** and **groups** likewise hash-derived from their
  defining tuple.

Don't introduce IDs that change across runs even when the input
doesn't. If the engine produces different output for byte-identical
inputs, that's a bug — see also the [why-no-LLM doc](docs/explanation/why-no-llm.md)
on determinism.

---

## When to update this file

Add a new section here when you establish a project-wide convention
during code review or while shipping a feature. Existing sections
can grow with examples; the goal is to keep "the way we do things
in this repo" findable.

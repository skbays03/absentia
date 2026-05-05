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
# or globally:
git config core.hooksPath .githooks
```

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

## 4. Local CI before commit

Run all four checks; commit only when all four pass:

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m ruff check src/ tests/
.venv/bin/python -m mypy src/lacuna
.venv/bin/python -m mkdocs build --strict
```

The CI pipeline runs the same four jobs in `.github/workflows/ci.yml`.
Pre-commit-running them locally avoids round-trips.

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

## 8. Stable IDs across runs

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

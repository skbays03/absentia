# Quickstart

Get from zero to your first gap in five minutes. We'll create a tiny
demo project, run lacuna on it, watch it find a real divergence,
suppress one, then point lacuna at your own code.

## Prerequisites

Lacuna isn't on PyPI yet (still pre-1.0). Install from a local clone:

```bash
git clone https://github.com/skbays03/lacuna.git
pipx install ./lacuna           # or `pip install ./lacuna` if you don't use pipx
```

After install, ``lacuna --version`` should work from any directory.
Requires Python 3.13+; works on macOS, Linux, and Windows.

## Step 1 — create a tiny demo project

We're going to set up a project where 4 of 5 API endpoints follow a
convention (the `@audit` decorator), and one doesn't. That's the
exact pattern lacuna is designed to find.

```bash
mkdir lacuna_demo
cd lacuna_demo
mkdir api
```

Create `decorators.py` (project root):

```python
def audit(fn):
    return fn
```

Create `api/users.py`:

```python
from decorators import audit

@audit
def create_user(): pass

@audit
def update_user(): pass

@audit
def list_users(): pass

@audit
def get_user(): pass

def delete_user(): pass
```

Five functions in `api/`, four decorated with `@audit`, one not.
That's a real divergence: 80% follow the convention, the fifth
doesn't.

## Step 2 — bootstrap lacuna in the project

```bash
lacuna init
```

This creates two things:

- `lacuna.toml` — config with sensible defaults
- `.lacuna/` — runtime state (auto-added to `.gitignore`)

## Step 3 — run your first scan

```bash
lacuna check
```

You should see something like:

```text
Scanning ~3 files (~200 B) — est. ~0.0 s at default jobs

✓ Walked corpus  ·  3 files  ·  0s
✓ Loaded store  ·  6 entities  ·  0s
✓ Mined rules  ·  3 rules, 1 candidate gaps  ·  0s
✓ Finalized  ·  1 gaps after dedup  ·  0s

GAPS                                              confidence ≥ 0.80   1

  api/users.py:15                          function `delete_user`           missing @audit                   0.80  g-XXXXXXX

────────────────────────────────────────────────────────────
  1 gaps  ·  3 rules

  6 entities scanned, 2 groups, 3 rules in 0.01s
```

(The five `✓` lines and the file-count preamble are the per-stage
progress display, shown when running interactively. They auto-suppress
in CI / piped output. Default `jobs=` is half your detected cores.)

Lacuna found:

- The headline **rule**: 4 of 5 functions in `api/` have `@audit`
  (confidence 0.80). The other two rules are about docstrings and
  type-annotation conventions inside `decorators.py` itself —
  separate convention checks lacuna runs by default.
- A **gap**: `delete_user` doesn't have `@audit` — that's the
  divergence the tutorial's setup is designed to surface.

The short ID `g-XXXXXXX` is your handle for this gap. Copy it.

## Step 4 — decide what to do

Two ways forward:

- *It's a real oversight* — fix the code (add `@audit` to
  `delete_user`), re-run `lacuna check`, the gap disappears.
- *It's intentional* — suppress with a reason.

Let's suppress it as if it were intentional:

```bash
lacuna suppress g-XXXXXXX --reason "delete_user is the audit endpoint itself"
```

Replace `g-XXXXXXX` with the ID from your output.

Re-run:

```bash
lacuna check
```

```text
No gaps. (lacuna found nothing wrong.)

  6 entities scanned, 2 groups, 3 rules in 0.01s (2 unchanged), 1 suppressed
```

The "1 suppressed" tells you lacuna found the gap but you've
explicitly silenced it. List your suppressions any time:

```bash
lacuna suppress --list
```

## Step 5 — explore in the TUI

```bash
lacuna
```

Bare `lacuna` (no subcommand, run from a terminal) opens the
interactive TUI. Switch views with the number keys, navigate rows
with `j` / `k`, and:

| Key | Action |
|---|---|
| `1` `2` `3` `4` | Gaps / Rules / Groups / Stats views |
| `Enter` | Open the file at the gap's line in `$EDITOR` |
| `s` | Suppress (modal asks for a reason) |
| `f` | Follow link — gap → rule → group |
| `Esc` | Walk back through the navigation stack |
| `Ctrl+R` | Rescan now |
| `w` | Toggle watch mode (auto-rescan every 2s) |
| `?` | Full keybinding reference |
| `q` | Quit |

If you set `$EDITOR` to your editor of choice, `Enter` jumps you
straight there. See the [TUI keybindings reference](../reference/tui-keys.md)
for the full list.

## Step 6 — try lacuna on your own project

```bash
cd /path/to/your/project
lacuna init
lacuna check
```

If your project has established conventions — decorator-heavy
framework code, class hierarchies, naming patterns — lacuna will
likely find a few real divergences. If your code is small or
intentionally heterogeneous, it might find nothing at the default
confidence threshold.

Loosen it to see weaker patterns:

```bash
lacuna check --min-confidence 0.6
```

Tighten it to filter to the strongest signals only:

```bash
lacuna check --min-confidence 0.95
```

> **Note** — if you skipped step 6 inside the `lacuna_demo/` project
> from earlier in this tutorial, you'll see "No gaps" because the
> step-4 suppression silenced the only divergence the demo had.
> Run `lacuna suppress g-XXXXXXX --remove` first (using the same gap
> ID you suppressed) to see how `--min-confidence 0.6` vs `0.95`
> changes which gaps surface. On a real project with a richer
> mining surface, the difference is more visible without that
> reset step.

## What just happened?

You created a project with a real (if simulated) pattern violation,
and lacuna found it. The engine ran four stages — *parse*, *group*,
*mine*, *compare* — entirely on your machine, in milliseconds, with
no model and no API. Every gap traced back to a rule, every rule
traced back to the members of your codebase that exhibit it.

That's the whole pitch. Try `lacuna est .` next to see a per-jobs
prediction of cold-scan time (it auto-improves as you run more
checks). For the longer version, see:

- [What is negative-space search?](../explanation/what-is-negative-space.md) —
  why this is a useful question to ask
- [How mining works](../explanation/how-mining-works.md) —
  the four-stage engine, with worked examples
- [Why no LLM?](../explanation/why-no-llm.md) —
  the deliberate-not-AI positioning
- [The cold-scan time estimator](../explanation/estimator.md) —
  what `lacuna est` actually predicts and how
- [Configuration reference](../reference/lacuna-toml.md) — every
  `lacuna.toml` option

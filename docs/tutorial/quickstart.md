# Quickstart

Get from zero to your first gap in five minutes. We'll create a tiny
demo project, run lacuna on it, watch it find a real divergence,
suppress one, then point lacuna at your own code.

## Prerequisites

```bash
pip install lacuna
```

After install, ``lacuna --version`` should work from any directory.
Requires Python 3.11+. (See the [README](../index.md) for source-install
and editable-install options.)

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
GAPS                                              confidence ≥ 0.80   1

  api/users.py:11                          function `delete_user`           missing @audit                   0.80  g-XXXXXXX

────────────────────────────────────────────────────────────
  1 gaps  ·  1 rules

  6 entities scanned, 2 groups, 1 rules in 0.00s
```

Lacuna found:

- A **rule**: 4 of 5 functions in `api/` have `@audit` (confidence 0.80)
- A **gap**: `delete_user` doesn't have it — that's the divergence

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

  6 entities scanned, 2 groups, 1 rules in 0.00s (2 unchanged), 1 suppressed
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

## What just happened?

You created a project with a real (if simulated) pattern violation,
and lacuna found it. The engine ran four stages — *parse*, *group*,
*mine*, *compare* — entirely on your machine, in milliseconds, with
no model and no API. Every gap traced back to a rule, every rule
traced back to the members of your codebase that exhibit it.

That's the whole pitch. For the longer version, see:

- [What is negative-space search?](../explanation/what-is-negative-space.md) —
  why this is a useful question to ask
- [How mining works](../explanation/how-mining-works.md) —
  the four-stage engine, with worked examples
- [Why no LLM?](../explanation/why-no-llm.md) —
  the deliberate-not-AI positioning
- [Configuration reference](../reference/lacuna-toml.md) — every
  `lacuna.toml` option

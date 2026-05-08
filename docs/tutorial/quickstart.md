# Quickstart

Get from zero to your first gap in five minutes. We'll create a tiny
demo project, run absentia on it, watch it find a real divergence,
suppress one, then point absentia at your own code.

## Prerequisites

The engine isn't on PyPI yet (still pre-1.0; the `absentia` name on
PyPI today is a metadata-only v0.0.1 placeholder). Install from a
local clone:

```bash
git clone https://github.com/skbays03/absentia.git
pipx install ./absentia           # or `pip install ./absentia` if you don't use pipx
```

After install, ``absentia --version`` should work from any directory.
Requires Python 3.13+; works on macOS, Linux, and Windows.

## Step 1 — create a tiny demo project

We're going to set up a project where 4 of 5 API endpoints follow a
convention (the `@audit` decorator), and one doesn't. That's the
exact pattern absentia is designed to find.

```bash
mkdir absentia_demo
cd absentia_demo
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

## Step 2 — bootstrap absentia in the project

```bash
absentia init
```

This creates two things:

- `absentia.toml` — config with sensible defaults
- `.absentia/` — runtime state (auto-added to `.gitignore`)

## Step 3 — run your first scan

```bash
absentia check
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

(The five `✓` lines and the file-count preamble are the runtime
progress display — `walk → parse → store → mine → finalize` — shown
when running interactively. They auto-suppress in CI / piped output.
Default `jobs=` is half your detected cores.)

Absentia found:

- The headline **rule**: 4 of 5 functions in `api/` have `@audit`
  (confidence 0.80). The other two rules are about docstrings and
  type-annotation conventions inside `decorators.py` itself —
  separate convention checks absentia runs by default.
- A **gap**: `delete_user` doesn't have `@audit` — that's the
  divergence the tutorial's setup is designed to surface.

The short ID `g-XXXXXXX` is your handle for this gap. Copy it.

## Step 4 — decide what to do

Two ways forward:

- *It's a real oversight* — fix the code (add `@audit` to
  `delete_user`), re-run `absentia check`, the gap disappears.
- *It's intentional* — suppress with a reason.

Let's suppress it as if it were intentional:

```bash
absentia suppress g-XXXXXXX --reason "delete_user is the audit endpoint itself"
```

Replace `g-XXXXXXX` with the ID from your output.

Re-run:

```bash
absentia check
```

```text
No gaps. (absentia found nothing wrong.)

  6 entities scanned, 2 groups, 3 rules in 0.01s (2 unchanged), 1 suppressed
```

The "1 suppressed" tells you absentia found the gap but you've
explicitly silenced it. List your suppressions any time:

```bash
absentia suppress --list
```

## Step 5 — explore in the TUI

```bash
absentia
```

Bare `absentia` (no subcommand, run from a terminal) opens the
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

## Step 6 — try absentia on your own project

```bash
cd /path/to/your/project
absentia init
absentia check
```

If your project has established conventions — decorator-heavy
framework code, class hierarchies, naming patterns — absentia will
likely find a few real divergences. If your code is small or
intentionally heterogeneous, it might find nothing at the default
confidence threshold.

Loosen it to see weaker patterns:

```bash
absentia check --min-confidence 0.6
```

Tighten it to filter to the strongest signals only:

```bash
absentia check --min-confidence 0.95
```

> **Note** — if you skipped step 6 inside the `absentia_demo/` project
> from earlier in this tutorial, you'll see "No gaps" because the
> step-4 suppression silenced the only divergence the demo had.
> Run `absentia suppress g-XXXXXXX --remove` first (using the same gap
> ID you suppressed) to see how `--min-confidence 0.6` vs `0.95`
> changes which gaps surface. On a real project with a richer
> mining surface, the difference is more visible without that
> reset step.

## What just happened?

You created a project with a real (if simulated) pattern violation,
and absentia found it. The engine ran four conceptual stages —
*parse*, *group*, *mine*, *compare* — entirely on your machine, in
milliseconds, with no model and no API. (The five `✓` lines you saw
above are the runtime view of this: `walk` and `finalize` bookend the
conceptual core, and `group` + `compare` fold into `mine`.) Every
gap traced back to a rule, every rule traced back to the members of
your codebase that exhibit it.

That's the whole pitch. Try `absentia est .` next to see a per-jobs
prediction of cold-scan time (it auto-improves as you run more
checks). For the longer version, see:

- [What is negative-space search?](../explanation/what-is-negative-space.md) —
  why this is a useful question to ask
- [How mining works](../explanation/how-mining-works.md) —
  the four-stage engine, with worked examples
- [Why no LLM?](../explanation/why-no-llm.md) —
  the deliberate-not-AI positioning
- [The cold-scan time estimator](../explanation/estimator.md) —
  what `absentia est` actually predicts and how
- [Configuration reference](../reference/absentia-toml.md) — every
  `absentia.toml` option

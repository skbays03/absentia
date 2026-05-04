# lacuna

> Find the holes your code already drew.

**lacuna** is a code-hygiene tool that mines patterns your codebase already follows
and surfaces the places that don't follow them. No rule files to write,
no model to train, no LLM in the loop — the rules come from your code itself.

If 9 of your 10 API endpoints use `@audit`, lacuna tells you about the 10th.
If 8 of your 10 panels have a corresponding test file, lacuna tells you about
the 2 that don't. Every gap traces back to the rule that produced it, and every
rule traces back to the members of your codebase that exhibit it.

```text
GAPS                                              confidence ≥ 0.80   3

▶ src/api/users.py:42       fn `delete_user`     missing @audit       0.90
  src/api/orders.py:15      fn `refund`          missing test pair    0.80
  panels/code_editor.py:842 fn `_render_gutter`  missing @lru_cache   0.83

DETAIL: g-7c91
  rule       r-a3f2  ·  fns in src/api/ have @audit
  support    9/10  (confidence 0.90)
  exhibits   create_user  update_user  list_users  get_user  …
  violator   ✗ delete_user
```

## Why this exists

Most code-hygiene tools answer one of two questions:

- **"Does this code violate a rule someone wrote?"**
  Linters and style checkers (ruff, ESLint, etc.). The rules come from a human or a config file.

- **"Is this code likely buggy?"**
  Static analyzers (mypy, pyright, sonarqube). The rules come from compiler theory or hand-coded heuristics.

Neither answers the question that actually keeps codebases consistent over years:
**"Does this code follow the patterns the rest of *this codebase* follows?"**

Most code drift isn't bugs and isn't style violations — it's a piece that diverged
from a convention nobody wrote down. lacuna mines the conventions and finds the
divergences. It's the difference between *"your `if` should have a space after it"*
(a global rule) and *"every other endpoint in this folder logs the user_id, this
one doesn't"* (a local pattern your team established without writing it down).

## Install

```bash
pip install lacuna
```

Requires Python 3.11+.

## Quickstart

From any project directory:

```bash
lacuna init      # create lacuna.toml + .lacuna/
lacuna           # open the TUI
```

That's it. lacuna scans your code, mines patterns, and shows you a navigable list
of gaps. Use `j`/`k` to move, `↵` to open in your editor, `s` to suppress with a
reason, `e` to see why a gap was flagged.

For CI and scripting:

```bash
lacuna check               # human-readable list
lacuna check --json        # machine-readable
lacuna check --max-gaps 0  # exit non-zero if any gaps remain
```

## What lacuna finds

Examples of typical gaps:

**Decorator inconsistency**

```text
src/api/users.py::delete_user
  missing  @audit decorator
  why      9/10 fns in src/api/ have @audit
```

**Missing sibling files**

```text
src/api/orders.py::refund
  missing  sibling test (tests/test_orders.py::test_refund)
  why      8/10 fns in src/api/ have a sibling test
```

**Inconsistent inheritance**

```text
panels/quirky_panel.py::QuirkyPanel
  missing  inherits from BasePanel
  why      12/14 classes in panels/ inherit from BasePanel
```

**Import omissions**

```text
tools/bug_cli.py
  missing  imports `from .common import setup_logging`
  why      9/11 files in tools/ import setup_logging
```

**Naming pattern breaks**

```text
tests/test_users.py::should_validate_email
  missing  starts with `test_`
  why      142/150 fns in tests/ follow `test_*`
```

## How it works

Four deterministic stages:

1. **Parse** — tree-sitter walks your code and extracts entities (functions,
   classes, files, imports, decorators).
2. **Group** — selectors organize entities into groups by directory, decorator,
   parent class, name pattern, or user-defined criteria.
3. **Mine** — within each group, frequency analysis finds features appearing in
   most members. Features above your confidence threshold become **rules**.
4. **Compare** — entities in a rule's group that don't satisfy its predicate
   become **gaps**.

Run lacuna twice on the same code and you get the same output. See
[how mining works](docs/explanation/how-mining-works.md) for the full picture.

## What lacuna is not

- **Not a linter.** Linters enforce rules someone else wrote. lacuna enforces
  rules *your codebase already follows*.
- **Not a code reviewer.** lacuna doesn't critique correctness, security, or
  design. It surfaces consistency gaps.
- **Not a fixer.** lacuna finds; humans fix. Auto-patching is a different
  product with very different tradeoffs.
- **Not AI.** No LLM, no embeddings, no model. Rules are statistical facts
  about your code, computed by counting. See [why no LLM](docs/explanation/why-no-llm.md).

## TUI vs CLI

Bare `lacuna` opens the **TUI** — the primary interface, built for exploration.
Drill from a gap to its rule, from a rule to its other members, from there to
*their* gaps. Filter live with `/`. Suppress with `s`. Watch mode (`w`) re-mines
on file change.

`lacuna check` is the **batch mode** for CI, scripting, and editor integrations.
It honors `--json`, `--max-gaps`, `--filter`, and exits with a meaningful status
code.

## Configuration

Per-project config in `lacuna.toml`:

```toml
[scan]
include   = ["src/", "tests/"]
exclude   = ["src/vendor/"]
languages = ["python"]

[mining]
min_confidence     = 0.8
min_group_size     = 5
max_predicate_size = 2

[selectors.directory]
enabled = true

[selectors.decorator]
enabled = true
exclude = ["@property", "@staticmethod"]
```

See [the configuration reference](docs/reference/lacuna-toml.md) for every option.

## Status

**Alpha.** lacuna is under active development. Public API and config format may
change before 1.0. Pin to an exact version if you depend on the output format.

## Documentation

- [Quickstart tutorial](docs/tutorial/quickstart.md)
- [What is negative-space search?](docs/explanation/what-is-negative-space.md)
- [How mining works](docs/explanation/how-mining-works.md)
- [Why no LLM](docs/explanation/why-no-llm.md)
- [Configuration reference](docs/reference/lacuna-toml.md)
- [CLI reference](docs/reference/cli.md)
- [TUI keybindings](docs/reference/tui-keys.md)

## License

Licensed under the [Apache License, Version 2.0](LICENSE). See [NOTICE](NOTICE) for attribution.

# lacuna

> **Find what you forgot to write.**
> *(The holes your code already drew.)*

Most code analyzers find what's wrong. **lacuna finds what's missing.**

Take 48 event handlers in your codebase. 47 call `bus.unsubscribe()` in their
cleanup paths. One doesn't. That one is a memory leak waiting for the user
who triggers the right interaction — and no linter, type-checker, or AI
reviewer will catch it, because nothing told them to expect that pattern.
Lacuna learns the pattern from the 47 and flags the outlier with a
0.94-confidence score.

Pattern mining over your AST. No LLM, no rule database, deterministic — same
input, same gaps. The rules come from your code itself: if 9 of your 10 API
endpoints use `@audit`, lacuna tells you about the 10th; if 8 of your 10
panels have a corresponding test file, lacuna tells you about the 2 that
don't. Every gap traces back to the rule that produced it, and every rule
traces back to the members of your codebase that exhibit it.

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

Lacuna isn't on PyPI yet (still pre-1.0). Install from the repo:

```bash
git clone https://github.com/skbays03/lacuna.git
cd lacuna
pip install .                   # or `pip install -e .` for an editable install
```

Or with [pipx](https://pipx.pypa.io/) (recommended for CLI tools — installs into an isolated environment, puts `lacuna` on your PATH):

```bash
git clone https://github.com/skbays03/lacuna.git
pipx install ./lacuna
```

Requires Python 3.13+. Cross-platform (macOS, Linux, Windows).
On Windows, the same `pip install .` / `pipx install ./lacuna`
commands work in PowerShell or `cmd`; if you want a venv first,
activate it with `.venv\Scripts\activate` instead of the
POSIX `source .venv/bin/activate`.

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
lacuna check               # human-readable list; exit 1 if any gaps
lacuna check --json        # machine-readable
lacuna check --max-gaps 5  # tolerate up to 5 gaps before failing the build
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

## Performance

lacuna scans the entire Linux kernel — 65,004 files / 686,923 entities
across ~30 million lines of C — in **~18 seconds** on an M-series
MacBook with default parallelism (parse: 7 s, mine: 11 s, finalize +
overhead: a couple more). Most projects scan in well under a second
of mining. Warm re-scans (incremental cache) complete in milliseconds.

The mining stage is the headline number: a name-indexed
``find_symmetry_gaps`` (no more O(P×N) per-pair-per-entity scan) plus
mypyc-compiled ``mining.py`` and ``symmetry.py`` together cut
mining-stage wall-clock on the kernel from ~5 minutes to ~11 seconds
— a **~30× speedup**, gap counts byte-identical.

If you're running on a free-threaded Python (3.13t / 3.14t), the
ThreadPool worker cap rises automatically from 4 to 7 (one per
mining strategy), unlocking another ~30 % when the C-extension
ecosystem catches up to the no-GIL ABI. No-op on regular CPython.

Full benchmark table covering all 17 built-in extractors (16
languages; TypeScript and TSX share a tree-sitter grammar but emit
distinct extractors) and ~2.4 M entities in
[architecture and performance](docs/explanation/architecture.md).

Curious what your machine looks like? Run `lacuna est` from any project
directory for a hardware-calibrated cold-scan estimate — see
[the estimator methodology](docs/explanation/estimator.md) for the math.

## What lacuna is not

- **Not a linter.** Linters enforce rules someone else wrote. lacuna enforces
  rules *your codebase already follows*.
- **Not a code reviewer.** lacuna doesn't critique correctness, security, or
  design. It surfaces consistency gaps.
- **Not a fixer.** lacuna finds; humans fix. Auto-patching is a different
  product with very different tradeoffs.
- **Not a resource-leak detector.** Patterns like `open()`/`close()`,
  `lock()`/`release()` — anything where the language or runtime defines the
  pair — are the linter's job. Use pylint, flake8-resource-leak, or
  Python's `with` statement for those. lacuna catches *project-specific*
  paired calls (your event-bus `subscribe`/`unsubscribe`, your custom
  audit `begin`/`commit`) — conventions no off-the-shelf linter knows.
- **Not a control-flow analyzer.** lacuna's read is coarse: "this function
  calls A but not B." It won't verify that B is called along every code
  path. Type systems and resource-leak linters own that layer.
- **Not AI.** No LLM, no embeddings, no model. Rules are statistical facts
  about your code, computed by counting. See [why no LLM](docs/explanation/why-no-llm.md).

## TUI vs CLI

Bare `lacuna` opens the **TUI** — the primary interface, built for exploration.
Drill from a gap to its rule, from a rule to its other members, from there to
*their* gaps. Filter live with `/`. Suppress with `s`. Watch mode (`w`) re-mines
on file change.

`lacuna check` is the **batch mode** for CI, scripting, and editor integrations.
It honors `--json`, `--max-gaps`, `--quiet`, and exits with a meaningful status
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

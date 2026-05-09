<!-- The hero block + tagline below are intentionally duplicated in
     docs/index.md. KEEP IN SYNC: any wording change here should land
     in docs/index.md too. -->

# absentia

> **Find what you forgot to write.**
> *(The holes your code already drew.)*

Most code analyzers find what's wrong. **absentia finds what's missing.**

Take 48 event handlers in your codebase. 47 call `bus.unsubscribe()` in their
cleanup paths. One doesn't. That one is a memory leak waiting for the user
who triggers the right interaction — and no linter, type-checker, or AI
reviewer will catch it, because nothing told them to expect that pattern.
Absentia learns the pattern from the 47 and flags the outlier with a
0.94-confidence score.

Pattern mining over your AST. No LLM, no rule database, deterministic — same
input, same gaps. The rules come from your code itself: if 9 of your 10 API
endpoints use `@audit`, absentia tells you about the 10th; if 8 of your 10
panels have a corresponding test file, absentia tells you about the 2 that
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

> *Real gap and rule IDs are seven characters after the prefix
> (`g-7c91234`, `r-a3f2bc7`); the 4-char forms above are shortened
> for readability.*

## Why this exists

Most code-hygiene tools answer one of two questions:

- **"Does this code violate a rule someone wrote?"**
  Linters and style checkers (ruff, ESLint, etc.). The rules come from a human or a config file.

- **"Is this code likely buggy?"**
  Static analyzers (mypy, pyright, sonarqube). The rules come from compiler theory or hand-coded heuristics.

Neither answers the question that actually keeps codebases consistent over years:
**"Does this code follow the patterns the rest of *this codebase* follows?"**

Most code drift isn't bugs and isn't style violations — it's a piece that diverged
from a convention nobody wrote down. absentia mines the conventions and finds the
divergences. It's the difference between *"your `if` should have a space after it"*
(a global rule) and *"every other endpoint in this folder logs the user_id, this
one doesn't"* (a local pattern your team established without writing it down).

## Install

Recommended — [`pipx`](https://pipx.pypa.io/) (installs absentia
into an isolated environment, puts the `absentia` command on your
PATH, won't pollute your system Python):

```bash
pipx install absentia
```

If you don't have `pipx` yet:

| OS | Install pipx |
|---|---|
| macOS | `brew install pipx && pipx ensurepath` |
| Debian / Ubuntu / WSL | `sudo apt install pipx && pipx ensurepath` |
| Fedora | `sudo dnf install pipx && pipx ensurepath` |
| Arch | `sudo pacman -S python-pipx && pipx ensurepath` |
| Windows | `python -m pip install --user pipx && python -m pipx ensurepath` |

After `pipx ensurepath`, open a new shell so the PATH update takes
effect.

### Plain `pip install` (for using absentia as a library)

```bash
pip install absentia
```

On modern Debian / Ubuntu / WSL you'll see:

```
error: externally-managed-environment
× This environment is externally managed
```

That's [PEP 668](https://peps.python.org/pep-0668/) — your distro's
system Python is protected. The right answer for any CLI tool is
`pipx` (above). If you specifically want to use absentia as a
library inside a project, create a venv first:

```bash
python3 -m venv .venv
source .venv/bin/activate     # PowerShell on Windows: .venv\Scripts\Activate.ps1
pip install absentia
```

### Requirements

Python 3.13+. Cross-platform (macOS, Linux, Windows). Pre-built
mypyc-compiled wheels for cp313 × {Linux x86_64, Linux aarch64,
macOS arm64, Windows AMD64}; other platforms (including cp314
and Intel Mac) install from sdist and compile via mypyc locally
at install time — same end result, slower first install.

### From source (development)

```bash
git clone https://github.com/skbays03/absentia.git
cd absentia
pip install -e ".[dev]"      # editable install + test deps
```

## Quickstart

From any project directory:

```bash
absentia init      # create absentia.toml + .absentia/
absentia           # open the TUI
```

That's it. absentia scans your code, mines patterns, and shows you a navigable list
of gaps. Use `j`/`k` to move, `↵` to open in your editor, `s` to suppress with a
reason, `e` to see why a gap was flagged.

For CI and scripting:

```bash
absentia check               # human-readable list; exit 1 if any gaps
absentia check --json        # machine-readable
absentia check --max-gaps 5  # tolerate up to 5 gaps before failing the build
absentia check --cold        # dev-time: ignore parse cache and re-parse the
                           # whole tree (or just `--cold src/foo.py` for one
                           # file). Useful when you suspect cache weirdness
                           # or are benchmarking the parse stage.
absentia check --language python,rust          # restrict to specific languages
absentia check --exclude '**/vendor/**'        # skip a glob pattern
absentia check --exclude tests --exclude docs  # multiple --exclude allowed
absentia --debug check                          # diagnostic prints to stderr
absentia --no-color check                       # force-disable ANSI color
```

Symmetric flags: `absentia est` accepts the same `--config`, `--jobs`,
`--json`, `--quiet`, `--language`, `--exclude`, `--cold` as `check`,
so muscle memory transfers between the two.

The full flag list (including `--config`, `--min-confidence`, est's
`--recalibrate` / `--use-synthetic` / `--history`, top-level
`--purge` / `--jobs-default`, and others) lives in the
[CLI reference](docs/reference/cli.md).

## What absentia finds

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

Four deterministic conceptual stages:

1. **Parse** — tree-sitter walks your code and extracts entities (functions,
   classes, files, imports, decorators).
2. **Group** — selectors organize entities into groups by directory, decorator,
   parent class, name pattern, or user-defined criteria.
3. **Mine** — within each group, frequency analysis finds features appearing in
   most members. Features above your confidence threshold become **rules**.
4. **Compare** — entities in a rule's group that don't satisfy its predicate
   become **gaps**.

Run absentia twice on the same code and you get the same output. The runtime
progress UI shows five stages — `walk → parse → store → mine → finalize` —
adding I/O bookends around the conceptual core; see
[architecture and performance](docs/explanation/architecture.md#the-pipeline)
for the full pipeline view, and [how mining works](docs/explanation/how-mining-works.md)
for the algorithm-deep walkthrough.

## Performance

absentia scans the entire Linux kernel — 65,004 files / 686,923 entities
across ~30 million lines of C — in **~24 seconds warm / ~48 seconds
cold** at default jobs on a 10-core M-series MacBook. Warm breakdown:
parse ~8 s (cache hits) + mine ~12 s + store ~2 s. Cold breakdown:
parse ~31 s + mine ~12 s + store ~3 s. Single-process baseline ~95 s
cold. Most projects on this scale rarely apply — typical real-world
codebases scan in seconds (and warm-rescan a small edited subset in
fractions).

The mining stage is the headline optimization story: a name-indexed
``find_symmetry_gaps`` (no more O(P×N) per-pair-per-entity scan) plus
mypyc-compiled ``mining.py`` and ``symmetry.py`` together cut
mining-stage wall-clock on the kernel from ~5 minutes to ~12 seconds
— a **~25× speedup**, gap counts byte-identical to the pre-
optimization baseline.

If you're running on a free-threaded Python (3.13t / 3.14t), the
ThreadPool worker cap rises automatically from 4 to 7 (one per
mining strategy) — more headroom for mining-stage parallelism
once the C-extension ecosystem catches up to the no-GIL ABI.
No-op on regular CPython. (Speedup percentage isn't pinned: most
tree-sitter wheels still ship GIL-only ABIs, so the path can't be
benchmarked end-to-end as of early 2026.)

Full benchmark table covering all 17 built-in extractors (16
languages; TypeScript and TSX share a tree-sitter grammar but emit
distinct extractors) — small smoke-test corpora plus the Linux
kernel as the big-corpus case study (687 k entities, ~30 M LOC of
C) — in [architecture and performance](docs/explanation/architecture.md).

Curious what your machine looks like? Run `absentia est` from any project
directory for a hardware-calibrated cold-scan estimate — see
[the estimator methodology](docs/explanation/estimator.md) for the math.

## What absentia is not

- **Not a linter.** Linters enforce rules someone else wrote. absentia enforces
  rules *your codebase already follows*.
- **Not a code reviewer.** absentia doesn't critique correctness, security, or
  design. It surfaces consistency gaps.
- **Not a fixer.** absentia finds; humans fix. Auto-patching is a different
  product with very different tradeoffs.
- **Not a resource-leak detector.** Patterns like `open()`/`close()`,
  `lock()`/`release()` — anything where the language or runtime defines the
  pair — are the linter's job. Use pylint, flake8-resource-leak, or
  Python's `with` statement for those. absentia catches *project-specific*
  paired calls (your event-bus `subscribe`/`unsubscribe`, your custom
  audit `begin`/`commit`) — conventions no off-the-shelf linter knows.
- **Not a control-flow analyzer.** absentia's read is coarse: "this function
  calls A but not B." It won't verify that B is called along every code
  path. Type systems and resource-leak linters own that layer.
- **Not AI.** No LLM, no embeddings, no model. Rules are statistical facts
  about your code, computed by counting. See [why no LLM](docs/explanation/why-no-llm.md).

## TUI vs CLI

Bare `absentia` opens the **TUI** — the primary interface, built for exploration.
Drill from a gap to its rule, from a rule to its other members, from there to
*their* gaps. Filter live with `/`. Suppress with `s`. Watch mode (`w`) auto-
rescans every 2 seconds — incremental, so unchanged files hit the parse cache.

`absentia check` is the **batch mode** for CI, scripting, and editor integrations.
It honors `--json`, `--max-gaps`, `--quiet`, and exits with a meaningful status
code.

## Configuration

Per-project config in `absentia.toml`:

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

See [the configuration reference](docs/reference/absentia-toml.md) for every option.

## Status

**Stable.** v1.0 ships the public API + config format covered by
SemVer; breaking changes go in major-version bumps. New gap detectors,
extractor improvements, and TUI features land in minor versions.

## Documentation

- [Quickstart tutorial](docs/tutorial/quickstart.md)
- [What is negative-space search?](docs/explanation/what-is-negative-space.md)
- [How mining works](docs/explanation/how-mining-works.md)
- [Why no LLM](docs/explanation/why-no-llm.md)
- [Configuration reference](docs/reference/absentia-toml.md)
- [CLI reference](docs/reference/cli.md)
- [TUI keybindings](docs/reference/tui-keys.md)

## License

Licensed under the [Apache License, Version 2.0](LICENSE). See [NOTICE](NOTICE) for attribution.

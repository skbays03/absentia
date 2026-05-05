# How mining works

Lacuna's engine has four stages:

1. **Parse** every source file into an AST.
2. **Group** the extracted entities by selector (directory, decorator,
   parent class, ...).
3. **Mine** each group: count how often each feature value appears
   among its members.
4. **Compare** values that pass the confidence threshold against the
   group's members. Members lacking the value become **gaps**.

Each stage is a few hundred lines of Python. There is no model. There
is no learned anything. The whole engine is counting and joining.

This doc walks each stage with concrete examples, explains the math,
and shows what lacuna's mining deliberately doesn't do.

## Stage 1: parse

Lacuna uses [tree-sitter](https://tree-sitter.github.io/) for every
language. Each per-language extractor walks the AST and yields
**entities** — discrete things lacuna can reason about (functions,
classes, methods, etc.) — paired with the **features** they exhibit.

For a Python file like:

```python
@audit
@app.route("/users")
def create_user():
    return Users.create(data)
```

the Python extractor emits:

```text
Entity(
    kind="function",
    qualified_name="src/api/users.py::create_user",
    file_path="src/api/users.py",
    line=3,
)
features = {
    "decorator": {"@audit", "@app.route"},   # args dropped
    "calls":     {"Users.create"},
}
```

Every language has its own extractor, but the output is the same
shape: an `Entity` and a `FeatureSet`. Mining doesn't know or care
which language an entity came from.

A few feature kinds aren't extractor-emitted — they're computed in
a corpus-level enrichment pass after all extractors run, because
they need to know about *other* entities to be answered. Right now
that's just one:

- **`sibling_test`** — true when a matching test entity exists
  (e.g. `tests/api/test_users.py::test_create_user` for the source
  function `src/api/users.py::create_user`). The check covers
  src/tests-mirror layouts, flat `tests/`, in-tree `test_*.py`,
  and Go-style `*_test.go`.

Mining `sibling_test` over the directory selector then surfaces
gaps like "8 of 10 functions in `src/api/` have a sibling test;
this one doesn't."

## A second mining strategy: symmetry pairs

Frequency mining catches "most do X, this doesn't." That's one
flavor of gap. There's another flavor that doesn't depend on what
the rest of the codebase does:

> A class with `__enter__` should have `__exit__`. The *concept*
> of an enter implies an exit. A single context-manager class
> with the asymmetry is a gap regardless of how many other
> classes use the protocol.

This is what the latin "lacuna" gestures at — a void implied by
everything around it, not just by frequency. The symmetry-pair
pass produces these gaps. It runs alongside frequency mining,
emits the same `Rule` and `Gap` shapes, but consults its own
configured pair table rather than the corpus's statistical
distribution.

Two sources of pairs:

**Hardcoded language protocols** (in `src/lacuna/symmetry.py`):

| Pair | Scope | Rule |
|---|---|---|
| `__enter__` / `__exit__` | class | Python's `with` requires both |
| `__aenter__` / `__aexit__` | class | `async with` requires both |

These are language contracts the runtime enforces — every Python
codebase that uses one needs the other, regardless of project
conventions.

**Auto-mined from the corpus** (`mine_symmetry_pairs`): pairs of
method or function names that co-occur in ≥80% of scopes
containing either one, with at least one violator. Catches
project-specific conventions without a hardcoded list:

- `setUp` / `tearDown` (when a project uses unittest)
- `upgrade` / `downgrade` (alembic migrations)
- `register` / `unregister` (your event bus)
- `acquire` / `release` (your custom session API)

The same engine philosophy applies — *"the rules come from your
code itself"* — extended to symmetry. A project that uses pytest
fixtures instead of unittest setUp/tearDown won't get spurious
pairs mined for it; the data won't be there.

For each pair the engine finds every scope (class or file) where
`left` is present, then flags any of those scopes that don't also
have `right`. The output reads naturally:

```text
src/contexts.py:6           method `BrokenContext.__enter__`  missing __exit__
migrations/0002_broken.py:1 function `upgrade`                missing downgrade
```

Symmetry pairs aren't gated by `min_confidence` — even a 1-of-1
violation surfaces (a single broken context-manager is the gap;
the rule isn't asking for a majority).

A few things worth noting:

- **Decorator arguments are dropped.** `@app.route("/users")` becomes
  `@app.route` so it groups with `@app.route("/orders")`. The point
  is to see which functions are decorated *with the same conceptual
  thing*, not which exact strings they share.
- **Stable, deterministic IDs.** `qualified_name` doubles as the
  entity's identity. Suppressions, gap IDs, and rule IDs all derive
  from these stable values, so they survive line shifts.
- **Tree-sitter is used as a parser only.** No LSP, no semantic
  analysis, no name resolution. Fast and language-agnostic.

## Stage 2: group

A **selector** is conceptually a function: `entities → list[Group]`.
Each selector emits zero or more groups; an entity can be in many
groups simultaneously.

Lacuna ships three built-in selectors:

| Selector | One group per | Example membership |
|---|---|---|
| `directory` | unique parent directory | "all functions/classes in `src/api/`" |
| `decorator` | unique decorator | "all entities with `@app.route`" |
| `parent_class` | unique parent class / protocol | "all classes that extend `BasePanel`" |

Given the entity above (a function in `src/api/users.py` with
`@audit` and `@app.route`), it joins three groups simultaneously:

- `directory::src/api/`
- `decorator::@audit`
- `decorator::@app.route`

Each group is mined independently. A pattern in `directory::src/api/`
doesn't know anything about `decorator::@audit` and vice versa — they
just happen to share members.

Selectors with very few members are dropped (default `min_members=3`)
because tiny groups produce statistical noise.

## Stage 3: mine

This is the heart of the engine, and it's a single counter.

Within each group, lacuna walks every member's `FeatureSet`,
**counts how often each feature value appears**, and divides by the
group size:

```text
confidence(value) = count_of_members_with(value) / group_size
```

A value with `confidence >= min_confidence` (default 0.8) becomes a
**rule**. That's it.

Concretely, for `directory::src/api/` with these 5 functions:

```text
create_user      decorator: {@audit, @app.route}
update_user      decorator: {@audit, @app.route}
list_users       decorator: {@audit, @app.route}
get_user         decorator: {@audit}
delete_user      decorator: {@app.route}
```

mining the `decorator` feature kind produces these counters:

```text
@audit:     4 / 5  →  confidence 0.80  →  RULE
@app.route: 4 / 5  →  confidence 0.80  →  RULE
```

Both pass the 0.80 threshold and become rules.

There's no pattern recognition, no clustering, no probabilistic
matching. The arithmetic is one division.

## Stage 4: compare

For each rule, lacuna walks the group's members again and emits a
**gap** for every member that *doesn't* have the rule's value:

```text
RULE @audit (4/5)
    ✓ create_user, update_user, list_users, get_user
    ✗ delete_user           ← gap

RULE @app.route (4/5)
    ✓ create_user, update_user, list_users, delete_user
    ✗ get_user              ← gap
```

A gap is `(rule_id, entity_id)`. That tuple, hashed, gives the short
ID you see in lacuna's output (`g-78bb4c8`).

The output you see in `lacuna check`:

```text
GAPS                                              confidence ≥ 0.80   2

  src/api/users.py:9   function `delete_user`  missing @audit       0.80  g-78bb4c8
  src/api/users.py:7   function `get_user`     missing @app.route   0.80  g-9d44a73
```

Both gaps are *real divergences from a real pattern*. Whether they're
*intentional* divergences (suppress them with a reason) or
*oversights* (fix them) is the human's job — lacuna's job is just
surfacing them.

## Eligibility-aware mining

Subtle but important: a member only counts toward a feature kind's
denominator if its FeatureSet *has that kind populated*. Functions
don't have `parent_class`; you can't be "missing" a feature kind that
doesn't apply to your entity kind.

Without this, mining `parent_class` over a directory with 8 classes
and 2 functions would say "10 members, 8 inherit from `Foo`,
confidence 0.8, the 2 functions are gaps." That'd be absurd —
functions can't extend classes.

The fix is small: when mining a feature kind, ignore members whose
FeatureSet doesn't have that kind set. The confidence numerator and
denominator both come from the eligible-members slice. If you have 8
eligible classes and 8 of them inherit from `Foo`, that's confidence
1.0, with the 2 functions correctly absent from both the support and
the gap list.

This is why you'll see different confidence numbers depending on how
heterogeneous a group is. The directory-group rule for `parent_class`
might be 8/8 (1.0) even though the directory has 10 members total.

## Self-reference filtering

One more refinement, also subtle: when the rule says "members of
`src/exceptions/` extend `HttpException`," and `HttpException`
itself is in `src/exceptions/`, the base class shouldn't be flagged
as missing itself. It can't extend itself in any language.

Lacuna detects this case (gap entity's leaf name == rule's feature
value, for `parent_class` rules) and silently drops it. This caught
real false positives during the 17-language audit (PHP/Slim was the
clearest example).

Note this only catches the *trivial* self-reference case. Multi-level
hierarchies (`HttpException` → `HttpSpecializedException` → specific
exceptions) can still produce "X missing Y" output where X is
genuinely a parent of Y in the chain. That's a real divergence,
just not a useful one — the right answer is suppress.

## Compound rules (planned, not yet shipped)

The current engine mines one feature value at a time:
*"members have `@audit`."* The next iteration will mine
**combinations** via [FP-growth](https://en.wikipedia.org/wiki/FP-growth_algorithm)
or similar:
*"members have BOTH `@audit` AND `@app.route`."*

This is gated by `mining.max_predicate_size` in `lacuna.toml`
(default 1, meaning single-feature rules only). When set higher,
lacuna will discover co-occurrence patterns — useful for catching
*"every endpoint should be both decorated and tested,"* not just one
or the other.

The math is still classical: frequent itemset mining has been
well-understood since the 1990s, when it was the original "data
mining" technique applied to retail basket analysis. We're using it
for code instead of supermarket purchases.

## What lacuna's mining doesn't do

Worth being explicit:

- **Lacuna doesn't understand semantics.** It doesn't know what
  `@audit` *means*. If 9/10 functions have a useless decorator, it
  flags the 10th as a gap with the same confidence as if they all
  had a critical decorator. Humans interpret.
- **Lacuna doesn't find bugs.** A function might have `@audit` and
  still be buggy. Use mypy, pyright, ruff, or semgrep for that.
- **Lacuna doesn't suggest fixes.** It surfaces divergences; you
  decide whether to fix or suppress. There's no "auto-apply" because
  the right action depends on context the tool can't see.
- **Lacuna doesn't track behavior over time** beyond the per-run
  counts. It doesn't know that this same gap appeared yesterday or
  three releases ago — every run is from the current state of the
  code.
- **Lacuna doesn't model multi-step reasoning.** It can't say "fix
  X, then Y becomes possible." Each gap stands alone.

These aren't limitations of lacuna's specific implementation; they're
limitations of frequency-based mining as a category. If you need any
of them, lacuna is the wrong tool. If you don't, the simplicity is a
feature: lacuna is fast, deterministic, and explainable specifically
because it does this one thing.

## Where to learn more

- [What is negative-space search?](what-is-negative-space.md) — the
  framing and value prop
- [Why no LLM?](why-no-llm.md) — why the engine is classical
- [Selectors reference](../reference/selectors.md) — every built-in
  selector, configurable in `lacuna.toml`
- The [`mining.py`
  source](https://github.com/skbays03/lacuna/blob/main/src/lacuna/mining.py)
  is ~120 lines including comments. The whole engine fits on one
  screen.

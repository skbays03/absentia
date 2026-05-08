# Selectors Reference

A *selector* turns the flat collection of extracted entities into
**groups** — sets of entities mining will compare against each
other. Absentia ships three built-in selector types, all configured
under `[selectors.*]` in `absentia.toml`.

## Built-in selectors

| Selector | Emits one group per | Default `min_members` |
|---|---|---|
| `directory` | unique parent directory containing entities | 3 |
| `decorator` | unique decorator value | 3 |
| `parent_class` | unique parent class / interface / protocol / trait | 3 |

### `directory`

Group entities by the directory they live in. The most useful
selector for "all entities in `src/api/` should look alike."

Options:

```toml
[selectors.directory]
enabled     = true
min_members = 3                       # skip dirs with fewer entities
kind_filter = ["function", "class"]   # only group these kinds
```

### `decorator`

Group entities by which decorator / annotation / attribute they
carry. Catches "all `@route`-decorated functions need `@audit`."

Options:

```toml
[selectors.decorator]
enabled     = true
min_members = 3
exclude     = ["@property", "@staticmethod", "@classmethod"]
```

> **Previewed but not yet wired:** the `include` allow-list and
> `match_args` toggle appear in `absentia.toml.example` as roadmap
> placeholders. The engine doesn't read them today; decorator
> arguments are always dropped (so `@app.route("/x")` groups with
> `@app.route("/y")` regardless), and all decorators not in
> `exclude` are eligible.

### `parent_class`

Group entities by inheritance / protocol conformance / trait impl.
Same selector handles classes (Python/Java/C#), structs (Rust), and
extensions (Swift/Kotlin) — anything tree-sitter exposes as a
parent-relationship.

Options:

```toml
[selectors.parent_class]
enabled     = true
min_members = 3
exclude     = ["object"]                # don't group on universal base
kind_filter = ["class", "struct", "enum", "extension", "protocol",
               "interface", "trait", "impl", "module", "record"]
```

> **Previewed but not yet wired:** the `include_inherited` toggle
> appears in `absentia.toml.example` as a roadmap placeholder. Today
> the engine groups by direct parent only; transitive inheritance
> chains aren't walked.

## How groups feed mining

For each group, the mining stage counts how often each feature
value appears among its members. Values that appear in
≥ `min_confidence` of members become **rules**; members that
*don't* exhibit the rule become **gaps**. See
[how mining works](../explanation/how-mining-works.md) for the
full mechanics.

## Adding new selectors

A community plugin SDK is planned; until it lands, the
`absentia.extractors` entry-point group is the only registration
mechanism for adding language extractors. New *selector* types
are still in-tree only.

See `absentia.toml.example` in the repo root for a working sample
with every option commented.

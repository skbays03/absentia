# `lacuna.toml` Reference

Lacuna reads `lacuna.toml` from the project root (or any ancestor
of the path you pass to `lacuna check`). Every section is optional;
defaults below are sensible starting points. `lacuna init` writes
a working `lacuna.toml` for you.

A working sample with comments lives at `lacuna.toml.example` in
the repo root.

## `[scan]` — what to look at

| Key | Type | Default | Meaning |
|---|---|---|---|
| `include` | list of glob | `["."]` | Directories or globs to scan. POSIX-style, relative to the project root. |
| `exclude` | list of glob | `[]` | Paths to skip. Use for vendored code, generated files, etc. |
| `languages` | list of string | all 16 supported | Which extractors to load. Limits scanning to source files of those languages. |

Supported `languages` values: `python`, `javascript`, `typescript`,
`tsx`, `rust`, `go`, `java`, `ruby`, `csharp`, `swift`, `c`, `cpp`,
`php`, `kotlin`, `scala`, `lua`, `bash`.

```toml
[scan]
include   = ["src/", "tests/"]
exclude   = ["src/vendor/", "**/generated/"]
languages = ["python"]
```

## `[mining]` — when to flag a divergence

| Key | Type | Default | Meaning |
|---|---|---|---|
| `min_confidence` | float | `0.8` | A feature value must appear in at least this fraction of an eligible group's members to become a rule. Higher = stricter; fewer but sturdier rules. |
| `min_group_size` | int | `3` | Groups smaller than this are skipped — too small for the statistics to mean anything. |

```toml
[mining]
min_confidence = 0.8
min_group_size = 5
```

CLI overrides: `lacuna check --min-confidence 0.9 --min-group-size 10`.

## `[selectors.*]` — how groups are formed

Selectors organize entities into groups whose members get compared
against each other. See the [selectors reference](selectors.md)
for the full per-selector explanation; this section is the schema.

### `[selectors.directory]`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `enabled` | bool | `true` | Toggle this selector. |
| `min_members` | int | `3` | Skip directories with fewer entities. |
| `kind_filter` | list of string | `["function", "class"]` | Only group entities of these kinds. |

### `[selectors.decorator]`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `enabled` | bool | `true` | Toggle this selector. |
| `min_members` | int | `3` | Skip decorators carried by fewer entities. |
| `exclude` | list of string | `["@property", "@staticmethod", "@classmethod"]` | Decorator values to ignore (typically universal builtins that wouldn't form interesting rules). |

### `[selectors.parent_class]`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `enabled` | bool | `true` | Toggle this selector. |
| `min_members` | int | `3` | Skip parent classes with fewer subclasses. |
| `exclude` | list of string | `["object", "Exception", ...]` | Parent classes too universal to group on. |
| `kind_filter` | list of string | class-like kinds across all 16 languages | Kinds eligible for grouping: `class`, `struct`, `enum`, `extension`, `protocol`, `interface`, `trait`, `impl`, `module`, `record`. |

```toml
[selectors.directory]
enabled     = true
min_members = 5
kind_filter = ["function", "class"]

[selectors.decorator]
enabled     = true
min_members = 3
exclude     = ["@property", "@staticmethod"]

[selectors.parent_class]
enabled     = true
min_members = 3
```

## Previewed in `lacuna.toml.example` but not yet wired

The example file previews several sections that are **not yet
implemented in the engine**: the `name_pattern` and `cluster`
selectors, `[[selectors.manual]]`, `[output]`, project-wide
`[[suppress]]` records, and `max_predicate_size` under `[mining]`.
Treat them as a roadmap; the sections above are what `lacuna check`
actually reads today.

For ad-hoc / personal suppressions (the only kind currently
supported), use `lacuna suppress <gap-id> --reason "..."` or press
`s` in the TUI. Those live in `.lacuna/state.db` and aren't
committed to version control.

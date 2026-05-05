# Selectors Reference

> _Stub. To be expanded into a full per-selector reference. The list of
> built-in selectors and their configuration lives in
> `lacuna.toml.example` at the repo root._

Lacuna ships three built-in selector types, configured under
`[selectors.*]` in `lacuna.toml`:

| Selector | Emits one group per | Default `min_members` |
|---|---|---|
| `directory` | unique parent directory containing entities | 3 |
| `decorator` | unique decorator value | 3 |
| `parent_class` | unique parent class / interface / protocol | 3 |

See [how mining works](../explanation/how-mining-works.md) for the
mechanics of how groups feed rules and gaps. See `lacuna.toml.example`
in the repo for every option each selector accepts.

A community plugin SDK for adding new selector types (and language
extractors) is planned; until it lands, the `lacuna.extractors`
entry-point group is the only registration mechanism.

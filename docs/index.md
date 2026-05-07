# lacuna

> **Find what you forgot to write.**
> *(The holes your code already drew.)*

Most code analyzers find what's wrong. **lacuna finds what's missing.** Pattern mining over your AST learns the conventions your codebase already follows and flags the places where one piece breaks ranks. No rule files to write, no model to train, no LLM in the loop — the rules come from your code itself.

## Get started

- **[Quickstart tutorial](tutorial/quickstart.md)** — install lacuna and find your first gaps in 5 minutes.

## Understand what it does

- **[What is negative-space search?](explanation/what-is-negative-space.md)** — the value prop in prose.
- **[Why no LLM?](explanation/why-no-llm.md)** — the positioning, defended.
- **[How mining works](explanation/how-mining-works.md)** — the mechanics behind every gap.
- **[Architecture and performance](explanation/architecture.md)** — the four-stage pipeline + benchmark numbers (Linux kernel: ~18 s end-to-end with default parallelism, ~11 s mining).
- **[Cold-scan time estimator](explanation/estimator.md)** — the math + calibration behind `lacuna est`.

## Look things up

- **[Configuration reference](reference/lacuna-toml.md)** — every `lacuna.toml` option.
- **[CLI reference](reference/cli.md)** — every command and flag.
- **[TUI keybindings](reference/tui-keys.md)** — every key, also surfaced via `?` in-app.
- **[Selectors reference](reference/selectors.md)** — the built-in selector types and their config.

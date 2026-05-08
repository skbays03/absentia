<!-- The hero block + intro paragraph below are intentionally duplicated
     in README.md (top of file). KEEP IN SYNC: any wording change here
     should land in README.md too. -->

# absentia

> **Find what you forgot to write.**
> *(The holes your code already drew.)*

Most code analyzers find what's wrong. **absentia finds what's missing.** Pattern mining over your AST learns the conventions your codebase already follows and flags the places where one piece breaks ranks. No rule files to write, no model to train, no LLM in the loop — the rules come from your code itself.

## Get started

- **[Quickstart tutorial](tutorial/quickstart.md)** — install absentia and find your first gaps in 5 minutes.

## Understand what it does

- **[What is negative-space search?](explanation/what-is-negative-space.md)** — the value prop in prose.
- **[Why no LLM?](explanation/why-no-llm.md)** — the positioning, defended.
- **[How mining works](explanation/how-mining-works.md)** — the mechanics behind every gap.
- **[Architecture and performance](explanation/architecture.md)** — the four-stage pipeline + benchmark numbers (Linux kernel: ~28 s warm / ~50 s cold at default jobs, mining ~14 s).
- **[Cold-scan time estimator](explanation/estimator.md)** — the math + calibration behind `absentia est`.

## Look things up

- **[Configuration reference](reference/absentia-toml.md)** — every `absentia.toml` option.
- **[CLI reference](reference/cli.md)** — every command and flag.
- **[TUI keybindings](reference/tui-keys.md)** — every key, also surfaced via `?` in-app.
- **[Selectors reference](reference/selectors.md)** — the built-in selector types and their config.

## Project info

- **[Changelog](https://github.com/skbays03/absentia/blob/main/CHANGELOG.md)** — per-release notes (Keep-a-Changelog format).
- **[Contributing](https://github.com/skbays03/absentia/blob/main/CONTRIBUTING.md)** — conventions for code, commits, and docs.
- **[Deferrals](https://github.com/skbays03/absentia/blob/main/DEFERRALS.md)** — known publication-blockers being tracked.
- **[Source on GitHub](https://github.com/skbays03/absentia)** — Apache 2.0 licensed.

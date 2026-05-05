# DEFERRALS

Items that would normally block publication of lacuna but which have been
intentionally deferred. Each entry notes what's needed, why it's deferred,
and the latest point it must be resolved.

> **How to use this file.** When you defer a publication-blocking decision
> during scoping, add it here under the appropriate severity section.
> When an item is resolved, **strike it through** rather than deleting it —
> the history of what was considered is itself useful context. Move struck
> items to the "Resolved" section at the bottom.

---

## Hard blockers (cannot publish v1.0 without resolving)

_None currently._

---

## Soft blockers (strongly expected before public marketing)

### Demo screenshot / GIF in README
- **Status:** README has a text-block placeholder under the tagline.
- **Why deferred:** TUI doesn't exist yet to record.
- **Resolution:** Once the TUI is functional, record an animated demo (asciinema → SVG, or a short MP4/GIF) and embed in the README directly under the tagline.
- **Latest by:** Before any v1.0 push.


### Public domain + docs site
- **Status:** README and `mkdocs.yml` reference `lacuna.dev` as a placeholder.
- **Why deferred:** Domain costs money; site needs deployment setup.
- **Resolution:** Acquire a domain (or commit to GitHub Pages at `username.github.io/lacuna/`), configure DNS, deploy mkdocs site. Update README + `mkdocs.yml` `site_url`.
- **Latest by:** Before any public marketing push.

### GitHub repo visibility (public timing)
- **Status:** Repo exists at <https://github.com/skbays03/lacuna> as **private** since 2026-05-04.
- **Why deferred:** Pre-alpha; not ready for public eyes.
- **Resolution:** Flip to public when v0.1 is usable end-to-end and the marketing-leaning docs (what-is-negative-space, why-no-llm, how-mining-works) are written. Run `gh repo edit --visibility public --accept-visibility-change-consequences`.
- **Latest by:** Before announcing the project anywhere.

---

## Nice-to-have before marketing


### Logo / wordmark
- **Status:** None.
- **Why deferred:** Cosmetic; doesn't block functionality.
- **Resolution:** Design or commission a simple wordmark. Add to README and docs site theme.
- **Latest by:** Optional; before any major marketing push.

---

## Resolved

### ~~CI pipeline~~ → `.github/workflows/ci.yml`
**Resolved 2026-05-05.**

Four jobs on every push and PR:
1. **test** — pytest matrix on Python 3.11, 3.12, 3.13
2. **lint** — `ruff check .`
3. **typecheck** — `mypy src/lacuna` (lenient settings, focused on real bugs not exhaustive generic annotations)
4. **docs-build** — `mkdocs build --strict` verifies the docs site builds cleanly

All four pass locally before commit. Doctest of tutorial examples deferred until the tutorial has real (executable) examples.

### ~~Performance benchmarks~~ → docs/explanation/architecture.md
**Resolved 2026-05-05.**

Benchmarked across 16 large public repos covering all supported
languages, totaling ~2.4M entities scanned. Headline: lacuna scans
the entire Linux kernel (666,574 entities, ~30M LOC of C) in 96.7s
on a single Python process on an M-series MacBook. Numbers, table,
and methodology in `docs/explanation/architecture.md`. Surfaced and
fixed a real bug along the way: 15 of 16 extractors had a recursive
call walker that overflowed Python's stack on deeply nested ASTs
(rust-lang/rust + dotnet/runtime). Generic iterative `walk_subtree`
in entities.py replaces the pattern uniformly.

### ~~Pre-code marketing docs~~ → all four shipped
**Resolved 2026-05-05.**

The trio of explanation docs (`what-is-negative-space.md`,
`why-no-llm.md`, `how-mining-works.md`) plus a runnable
`tutorial/quickstart.md`. Combined ~3,800 words of prose answering
the three questions that decide whether someone adopts lacuna: what
is this, can I trust it, how does it work. Quickstart was verified
end-to-end: every command in the tutorial was executed in sequence
and produced the documented output.

### ~~License~~ → Apache 2.0
**Resolved 2026-05-04.**

Original consideration: MIT, Apache 2.0, AGPL, or dual-license (community AGPL + paid commercial). Chose **Apache 2.0** for adoption priority over protection — lacuna isn't readily hostable as a SaaS so AGPL's network protection doesn't apply; the patent grant matters for corporate adoption; and an Apache-licensed core doesn't preclude future commercial tier-2/tier-3 offerings layered on top.


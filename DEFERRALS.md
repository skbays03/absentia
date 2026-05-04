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

### Pre-code marketing docs
- **Status:** README done; `tutorial/quickstart.md`, `explanation/what-is-negative-space.md`, `explanation/why-no-llm.md`, and `explanation/how-mining-works.md` are stubs.
- **Why deferred:** They'll write better once the engine is real enough to demo against.
- **Resolution:** Fill in each stub with real content. (See `project_lacuna_docs_to_scaffold.md` in memory for the full pending list.)
- **Latest by:** Before any v1.0 push.

### Public domain + docs site
- **Status:** README and `mkdocs.yml` reference `lacuna.dev` as a placeholder.
- **Why deferred:** Domain costs money; site needs deployment setup.
- **Resolution:** Acquire a domain (or commit to GitHub Pages at `username.github.io/lacuna/`), configure DNS, deploy mkdocs site. Update README + `mkdocs.yml` `site_url`.
- **Latest by:** Before any public marketing push.

### GitHub repo visibility
- **Status:** Local-only; not on GitHub.
- **Why deferred:** Pre-alpha; not ready for public eyes.
- **Resolution:** Decide private→public timing. Reasonable to make public when v0.1 is usable end-to-end.
- **Latest by:** Before announcing the project anywhere.

---

## Nice-to-have before marketing

### Performance benchmarks
- **Status:** No real numbers.
- **Why deferred:** Engine doesn't exist yet.
- **Resolution:** Once MVP runs, benchmark on Dev-Dashboard (~750 files) and a larger corpus (~10k files). Publish results in `docs/explanation/architecture.md` and reference from README.
- **Latest by:** Before any v1.0 push.

### Logo / wordmark
- **Status:** None.
- **Why deferred:** Cosmetic; doesn't block functionality.
- **Resolution:** Design or commission a simple wordmark. Add to README and docs site theme.
- **Latest by:** Optional; before any major marketing push.

### CI pipeline
- **Status:** No `.github/workflows/` yet.
- **Why deferred:** No code to test yet.
- **Resolution:** Once tests exist, add workflows for: pytest on push/PR, ruff + mypy lint, mkdocs build verification, doctest of tutorial examples.
- **Latest by:** Before public repo (so PRs run CI).

---

## Resolved

### ~~License~~ → Apache 2.0
**Resolved 2026-05-04.**

Original consideration: MIT, Apache 2.0, AGPL, or dual-license (community AGPL + paid commercial). Chose **Apache 2.0** for adoption priority over protection — lacuna isn't readily hostable as a SaaS so AGPL's network protection doesn't apply; the patent grant matters for corporate adoption; and an Apache-licensed core doesn't preclude future commercial tier-2/tier-3 offerings layered on top.


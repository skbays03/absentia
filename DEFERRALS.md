# DEFERRALS

Items that would normally block publication of absentia but which have been
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
- **Status:** TUI is functional and the CLI now has per-stage progress UI worth recording. README still has a text-block placeholder.
- **Why deferred:** Recording asset still needs to be produced.
- **Resolution:** Record an animated demo (asciinema → SVG, or a short MP4/GIF) showing either: (a) the TUI at work in a real codebase, or (b) `absentia check` on a kernel-sized corpus showing the per-stage progress display + ✓ summary lines. Embed in the README directly under the tagline.
- **Latest by:** Before any v1.0 push.


### Public domain + docs site
- **Status:** README and `mkdocs.yml` reference `absentia.dev` as a placeholder.
- **Why deferred:** Domain costs money; site needs deployment setup.
- **Resolution:** Acquire a domain (or commit to GitHub Pages at `username.github.io/absentia/`), configure DNS, deploy mkdocs site. Update README + `mkdocs.yml` `site_url`.
- **Latest by:** Before any public marketing push.

### GitHub repo visibility (public timing)
- **Status:** Repo exists at <https://github.com/skbays03/absentia> as **private** since 2026-05-04.
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

## Optimization items considered and deferred

Performance items walked through during the 2026-05-07 optimization-plan
review and explicitly passed on. None are publication-blockers — they're
recorded here so future-us doesn't re-discover the same conclusions, and
so the *revisit triggers* are preserved alongside the rejection.

Source: `~/Desktop/lacuna_optimization_plan.txt`.

### Optimization #6 — Pipeline streaming (overlap parse with mining)
- **Status:** Considered, passed 2026-05-07.
- **Why deferred:** Original framing assumed mining was the 320 s anchor on the kernel critical path. Post-optimization #3 (mining-stage 30× speedup, commits `2af1db8` + `dcfd7f0` + `f3cbc51` + `37ce834`), kernel mining is ~10 s of a ~25-30 s end-to-end scan; best-case streaming wins ~25% wall-clock for an invasive storage-layer rewrite. Most mining work isn't streamable anyway (decorator/parent_class selectors are corpus-wide; symmetry, call-pair, and series-gap detection all need the full entity universe), and `enrich_sibling_tests` blocks any sibling-test-dependent mining until the full corpus is parsed.
- **Revisit when:** Either a future detector reverses the mining/parse cost ratio, *or* the early-results UX win becomes valuable enough to justify on its own (in which case scope it as a UX change, not a streaming pipeline).

### Optimization #8 — Incremental enrichment cache
- **Status:** Considered, passed 2026-05-07. Profile-validated 2026-05-07: original proposal was the *wrong fix* for the right problem. Consumer-loop optimization landed instead (see CHANGELOG `[Unreleased]` Performance section).
- **Why deferred:** Original framing assumed the cost was in `build_test_method_index` (the index build) — caching the index across rescans was the proposed fix. The cProfile run on cold kernel scan showed `build_test_method_index` doesn't even appear in the top-25 hotspots; the actual ~7.8 s of enrichment cost lives in the *consumer loop* (per-entity calls to `is_test_file` + `_candidate_test_files`). A persisted index cache would have saved ~half a second while inheriting the full `EXTRACTOR_FINGERPRINT`-style fingerprint + CI-gate + storage-seam architectural mass. The actual win came from in-process per-file memoization of the consumer-loop helpers, no persistence required.
- **Revisit when:** Either (a) `enrich_all` grows past 2 enrichment passes (the compounding case for a persisted index), *or* (b) the in-process memoization stops being enough as new enrichment kinds add more per-entity work.

### Optimization #9 — PyPy compatibility check
- **Status:** Considered, passed 2026-05-07.
- **Why deferred:** Ruled out structurally by the mypyc dependency. `wheels.yml` builds `mining.py` + `symmetry.py` as native CPython C extensions; PyPy can't load them and would silently fall through to the pure-Python sdist install — regressing from the 30× mining speedup landed in #3. PyPy also doesn't support the free-threaded Python builds (3.13t/3.14t) that `mining_worker_cap()` relies on for worker scaling. Even tree-sitter's C bindings would run via the slow cpyext path. The plan's "1-hour test, near-zero risk" framing pre-dated all three of those commitments.
- **Revisit when:** Only if mypyc is removed from the build (no current plan to do so) — PyPy and mypyc-compiled wheels are mutually exclusive.

### Optimization #11 — Persistent worker daemon (mypy --daemon style)
- **Status:** Considered, passed 2026-05-07.
- **Why deferred:** Solves a problem that doesn't exist for users who don't exist. The plan named "watch mode, editor integrations" as the target use case; neither has been built and neither is on the near-term roadmap. The TUI (locked-in decision #2 — "TUI is the primary UX") already holds extractors + entity store in memory across rescans, covering the "warm iteration" niche today. Locked-in decision #6 ("architectural seams designed for worst-case; implementation built for current case") explicitly chose engine-library + one-shot-CLI as the two consumer seams; a daemon IPC seam isn't on that list and would invert the deliberate boundary. Stale-state risk (daemon serves cached entities while files mutate underneath) erodes the determinism that's the project's core promise (locked-in decision #1). Cold-start tax of ~600 ms-1.5 s is <5% of any non-trivial scan.
- **Revisit when:** A real watch-mode feature ships (file-watcher + incremental rescan loop), *or* an editor/IDE integration that drives `absentia check` per save lands. The daemon should be motivated by a concrete consumer, not built in anticipation of one.

### Optimization #10 — Cython / Rust port of hot helpers
- **Status:** Considered, passed 2026-05-07.
- **Why deferred:** The biggest target named by the plan ("per-pair counter loop in symmetry/call-pair mining") was already absorbed by the mypyc compilation of `mining.py` + `symmetry.py` shipped in #3. The two remaining named helpers — `walk_subtree` and `clean_call_name` in `src/absentia/entities.py` — have a low realistic ceiling: `walk_subtree` is bottlenecked by `node.children` calls into the tree-sitter C extension (mypyc/Cython/Rust can't speed up calls *into* C); `clean_call_name` is hot but cheap (~1-2 s of a ~25-30 s end-to-end kernel scan). The mypyc-include variant (extending the include list to `entities.py`) is the only ROI-sane path but risks a typing-cascade refactor across all 17 extractor subclasses. The plan's own sequencing rule was "only after structural wins exhausted *and* use #12 profile-guided pickup to inform which" — neither precondition met.
- **Revisit when:** Either (a) profile-guided pickup (#12) actually fingers `walk_subtree` or `clean_call_name` as a top-3 hotspot, *or* (b) parse-stage perf re-emerges as user-visible. Even then, prefer the mypyc-include variant over Cython/Rust to avoid duplicating the build toolchain.

---

## Resolved

### ~~CI pipeline~~ → `.github/workflows/ci.yml`
**Resolved 2026-05-05.**

Four jobs on every push and PR:
1. **test** — pytest matrix on Python 3.11, 3.12, 3.13
2. **lint** — `ruff check .`
3. **typecheck** — `mypy src/absentia` (lenient settings, focused on real bugs not exhaustive generic annotations)
4. **docs-build** — `mkdocs build --strict` verifies the docs site builds cleanly

All four pass locally before commit. Doctest of tutorial examples deferred until the tutorial has real (executable) examples.

### ~~Performance benchmarks~~ → docs/explanation/architecture.md
**Resolved 2026-05-05.**

Benchmarked across 16 large public repos covering all supported
languages, totaling ~2.4M entities scanned. Headline: absentia scans
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
the three questions that decide whether someone adopts absentia: what
is this, can I trust it, how does it work. Quickstart was verified
end-to-end: every command in the tutorial was executed in sequence
and produced the documented output.

### ~~License~~ → Apache 2.0
**Resolved 2026-05-04.**

Original consideration: MIT, Apache 2.0, AGPL, or dual-license (community AGPL + paid commercial). Chose **Apache 2.0** for adoption priority over protection — absentia isn't readily hostable as a SaaS so AGPL's network protection doesn't apply; the patent grant matters for corporate adoption; and an Apache-licensed core doesn't preclude future commercial tier-2/tier-3 offerings layered on top.

